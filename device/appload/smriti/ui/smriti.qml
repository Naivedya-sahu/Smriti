import QtQuick 2.5
import QtQuick.Controls 2.5

// Smriti toolbox — AppLoad frontend-only app. It is the MASTER SWITCH.
//
// Opening this app from the drawer ARMS Smriti (writes 1 into
// /home/root/.smriti/armed): only then does the floating eye appear in
// notebooks, the screen streamer run, and sessions become possible. Before
// arming — and after "Deactivate" — there is no eye and no usage anywhere.
//
// Talks to the Monke daemon through files (QML XHR can read/write file://
// URLs): writes start/stop into /home/root/.smriti/eye-cmd, arm state into
// /home/root/.smriti/armed, reads daemon state from /home/root/.smriti/state
// and the brain stage from /tmp/smriti-stage ("<epoch-seconds> <stage>").
// smriti-eye-watch bridges those files to the daemon and drives the streamer.
//
// Slim UI (Navy): status, start/stop, brain, eye placement.

Rectangle {
    id: root
    anchors.fill: parent
    color: "white"

    property bool watching: false
    property bool armed: false
    // brain status: "" = offline (missing, unreadable or stale stage file)
    property string stage: ""
    readonly property int stageMaxAge: 30   // seconds
    readonly property bool stageThinking: root.stage === "reading" || root.stage === "thinking" || root.stage === "settling" || root.stage === "confirming"
    readonly property bool stageDrawing: root.stage === "inking"
    readonly property string eyeState: !root.armed ? "off" : (root.stageDrawing ? "drawing" : (root.stageThinking ? "thinking" : (root.watching ? "watching" : "idle")))
    property int statusPulse: 0

    // AI settings -> /home/root/.smriti/settings.json (daemon reads it every
    // turn). Same 9 keys as always; task type and context are fixed "auto".
    property string savedNote: ""
    property var cloud: []              // [{id, ready}] from models.json
    // Claude routes = host/brain.py CLAUDE_ALIASES; models.json "cloud" lists
    // them as bare aliases next to "chatgpt:<id>" entries.
    readonly property var claudeRoutes: ["haiku", "sonnet", "opus"]
    property string reader: "chatgpt"
    property string writer: "chatgpt"
    // legacy "bottom-right" | "bottom-left" | "top-middle", or "x,y" fractions
    property string eye: "bottom-right"
    // Kept as an explicit compatibility contract for deployed older eye QMDs.
    readonly property var legacyEyeNames: ["bottom-right", "bottom-left", "top-middle"]
    readonly property var eyeF: root.eyeFrac(root.eye)
    // Escalation is deferred: "escalate" is round-tripped and escalate_next is
    // always written false, so the app never re-arms the host's one-shot flag.
    property string escalate: "sonnet"


    // Eye quick presets: 4 corners + 4 edge midpoints (fractions of the page).
    readonly property var eyePresets: [
        { l: "top-left", x: 0, y: 0 }, { l: "top", x: 0.5, y: 0 }, { l: "top-right", x: 1, y: 0 },
        { l: "left", x: 0, y: 0.5 }, { l: "right", x: 1, y: 0.5 },
        { l: "bottom-left", x: 0, y: 1 }, { l: "bottom", x: 0.5, y: 1 }, { l: "bottom-right", x: 1, y: 1 }
    ]

    function saveSettings(note) {
        writeFile("/home/root/.smriti/settings.json", JSON.stringify({
            model: "", profile: "auto", prompt: "",
            reader: root.reader, writer: root.writer, context: "auto",
            escalate: root.escalate, escalate_next: false, eye: root.eye }));
        root.savedNote = note + " saved";
    }

    // The host runs anything that is not a Claude alias ("chatgpt",
    // "chatgpt:<id>", legacy "local" / "") on ChatGPT; saved values are kept
    // unchanged until the brain toggle is tapped.
    function routeKey(v) {
        return root.claudeRoutes.indexOf(v) >= 0 ? v : "chatgpt";
    }

    // first Claude alias models.json publishes ("" = none -> Claude hidden)
    function claudeAlias() {
        for (var j = 0; j < root.cloud.length; j++)
            if (root.claudeRoutes.indexOf(root.cloud[j].id) >= 0) return root.cloud[j].id;
        return "";
    }

    function brainChoices() {
        var out = [{ v: "chatgpt", label: "ChatGPT", ok: true }];
        var alias = root.claudeAlias();
        if (alias !== "") {
            var ready = true;
            for (var j = 0; j < root.cloud.length; j++)
                if (root.cloud[j].id === alias) ready = root.cloud[j].ready !== false;
            out.push({ v: "claude", label: "Claude" + (ready ? "" : " (login needed)"), ok: ready });
        }
        return out;
    }

    // highlighted brain; a mixed legacy reader/writer pair highlights none
    function brainKey() {
        var r = root.routeKey(root.reader), w = root.routeKey(root.writer);
        if (r !== w) return "";
        return r === "chatgpt" ? "chatgpt" : "claude";
    }

    function setBrain(v) {
        var val = v === "claude" ? root.claudeAlias() : "chatgpt";
        if (val === "") return;
        root.reader = val; root.writer = val;
        root.saveSettings("brain");
    }


    // "<epoch-seconds> <stage>" -> stage, or "" when missing/garbled/stale
    function stageFrom(t, nowSec) {
        var m = String(t || "").trim().split(/\s+/);
        var ts = parseFloat(m[0]);
        if (m.length < 2 || isNaN(ts) || nowSec - ts > root.stageMaxAge) return "";
        return m[1];
    }

    // settings "eye" -> [fx, fy]; mirrors smriti-eye.qmd smritiPosDone
    function eyeFrac(e) {
        if (e === "bottom-left") return [0, 1];
        if (e === "top-middle") return [0.5, 0];
        var m = String(e || "").split(",");
        if (m.length === 2 && !isNaN(parseFloat(m[0])) && !isNaN(parseFloat(m[1])))
            return [Math.max(0, Math.min(1, parseFloat(m[0]))),
                    Math.max(0, Math.min(1, parseFloat(m[1])))];
        return [1, 1];
    }

    // legacy names where they exist (an older eye qmd understands only
    // those), else "x,y" fractions of the free page area
    function eyeValue(fx, fy) {
        if (fx === 1 && fy === 1) return "bottom-right";
        if (fx === 0 && fy === 1) return "bottom-left";
        if (fx === 0.5 && fy === 0) return "top-middle";
        return fx.toFixed(3) + "," + fy.toFixed(3);
    }

    function placeEye(fx, fy) {
        fx = Math.round(Math.max(0, Math.min(1, fx)) * 1000) / 1000;
        fy = Math.round(Math.max(0, Math.min(1, fy)) * 1000) / 1000;
        root.eye = root.eyeValue(fx, fy);
        root.saveSettings("eye");
    }

    function loadSettings() {
        loadModels();
        readSettingsFile();
    }

    // Polled every 3 s so newly available Claude routes are reflected promptly.
    function loadModels() {
        readFile("/home/root/.smriti/models.json", function(t) {
            var m = null;
            try { m = JSON.parse(t); } catch (e) { m = null; }
            root.cloud = m ? (m.cloud || []) : [];
        });
    }

    function readSettingsFile() {
        readFile("/home/root/.smriti/settings.json", function(t) {
            var s = null;
            try { s = JSON.parse(t); } catch (e) { s = null; }
            if (!s) return;
            root.reader = s.reader || "chatgpt";
            root.writer = s.writer || "chatgpt";
            root.eye = s.eye || "bottom-right";
            root.escalate = s.escalate || "sonnet";
        });
    }

    signal close
    function unloading() {
        // Leaving the app does NOT disarm — you arm here, then go write in a
        // notebook. Disarm is the explicit "Deactivate" button only.
    }

    function writeFile(path, body) {
        var xhr = new XMLHttpRequest();
        xhr.open("PUT", "file://" + path);
        xhr.send(body);
    }

    function readFile(path, cb) {
        var xhr = new XMLHttpRequest();
        xhr.open("GET", "file://" + path);
        xhr.onreadystatechange = function() {
            if (xhr.readyState === XMLHttpRequest.DONE) cb(xhr.responseText);
        };
        xhr.send();
    }

    function refresh() {
        readFile("/home/root/.smriti/state", function(t) {
            root.watching = (t.indexOf("watching") === 0);
        });
        readFile("/home/root/.smriti/armed", function(t) {
            root.armed = (t.indexOf("1") === 0);
        });
        readFile("/tmp/smriti-stage", function(t) {
            root.stage = root.stageFrom(t, Date.now() / 1000);
        });
        loadModels();
    }

    function setArmed(on) {
        root.armed = on;
        writeFile("/home/root/.smriti/armed", on ? "1" : "0");
        if (!on) root.watching = false;   // disarm ends the session too
    }

    function command(cmd) {
        writeFile("/home/root/.smriti/eye-cmd", cmd);
        root.watching = (cmd === "start");
    }

    // Opening the app arms Smriti.
    Component.onCompleted: { setArmed(true); loadSettings(); }

    // polls only while the app is visible
    Timer {
        interval: 3000
        running: root.visible
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refresh()
    }

    Timer {
        interval: 280
        running: root.visible && root.stageThinking
        repeat: true
        onTriggered: root.statusPulse = (root.statusPulse + 1) % 3
    }

    Flickable {
        id: flick
        // leave the top edge free: AppLoad's swipe-down (minimize / close)
        // starts there, and a full-screen Flickable swallowed it (Navy)
        anchors.fill: parent
        anchors.topMargin: 110
        contentWidth: width
        contentHeight: panel.height + 60
        clip: true
        boundsBehavior: Flickable.StopAtBounds
    Column {
        id: panel
        x: 80
        y: 0
        width: flick.width - 160
        spacing: 22

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Smriti"
            font.pointSize: 42
            font.bold: true
        }

        // ── state: same symbols as the notebook eye ─────────────────────────
        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 18
            Rectangle {
                width: 58; height: 58; radius: 29; color: "white"
                border.color: "black"; border.width: 3
                Rectangle { anchors.centerIn: parent; width: 25; height: 5; radius: 3; color: "black"
                            visible: root.eyeState === "idle" }
                Rectangle { anchors.centerIn: parent; width: 16; height: 16; radius: 8; color: "black"
                            visible: root.eyeState === "watching" }
                Row { anchors.centerIn: parent; spacing: 4; visible: root.eyeState === "thinking"
                    Repeater { model: 3
                        Rectangle { width: 7; height: 7; radius: 4; color: "black"
                                    opacity: root.statusPulse === index ? 1.0 : 0.25 }
                    }
                }
                Rectangle { anchors.centerIn: parent; width: 25; height: 6; radius: 2; color: "black"
                            rotation: -45; visible: root.eyeState === "drawing" }
            }
            Column {
                spacing: 2
                Text { font.pointSize: 20; font.bold: true
                    text: root.eyeState === "off" ? "Inactive" :
                          (root.eyeState === "idle" ? "Eye closed · idle" :
                           (root.eyeState === "watching" ? "Eye open · listening" :
                            (root.eyeState === "thinking" ? "Thinking…" : "Drawing reply…"))) }
                Text { font.pointSize: 17; color: root.stage !== "" ? "black" : "#666666"
                    text: root.stage !== "" ? "Monke online · " + root.stage : "Monke offline (no brain reachable)" }
            }
        }

        // ── start / stop + master off switch ───────────────────────────────
        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 24
            Rectangle {
                width: 260; height: 72; radius: 12; color: "black"
                Text {
                    anchors.centerIn: parent; color: "white"; font.pointSize: 20; font.bold: true
                    text: !root.armed ? "Activate" : (root.watching ? "Stop" : "Start")
                }
                MouseArea {
                    anchors.fill: parent
                    onClicked: {
                        if (!root.armed) { root.setArmed(true); return; }
                        root.command(root.watching ? "stop" : "start");
                    }
                }
            }
            // hides the notebook eye, stops the streamer, ends the session
            Rectangle {
                width: 300; height: 72; radius: 12; color: "white"
                border.color: "black"; border.width: 3
                visible: root.armed
                Text { anchors.centerIn: parent; text: "Deactivate Smriti"; font.pointSize: 18 }
                MouseArea { anchors.fill: parent; onClicked: root.setArmed(false) }
            }
        }

        // ── brain: one route for both reading and writing ──────────────────
        Text { text: "Brain"; font.pointSize: 20; font.bold: true }
        Flow {
            width: panel.width; spacing: 14
            Repeater {
                model: root.brainChoices()
                Rectangle {
                    width: bl.width + 48; height: 64; radius: 10
                    color: root.brainKey() === modelData.v ? "black" : "white"
                    border.color: "black"; border.width: 3
                    opacity: modelData.ok ? 1.0 : 0.45
                    Text { id: bl; anchors.centerIn: parent; font.pointSize: 16; text: modelData.label
                           color: root.brainKey() === modelData.v ? "white" : "black" }
                    MouseArea { anchors.fill: parent; onClicked: root.setBrain(modelData.v) }
                }
            }
        }


        // ── eye placement: tap the mini page, or a preset ──────────────────
        Text { text: "Eye placement"; font.pointSize: 20; font.bold: true }
        Flow {
            width: panel.width; spacing: 10
            Repeater {
                model: root.eyePresets
                Rectangle {
                    width: el.width + 32; height: 56; radius: 10
                    color: root.eye === root.eyeValue(modelData.x, modelData.y) ? "black" : "white"
                    border.color: "black"; border.width: 3
                    Text { id: el; anchors.centerIn: parent; font.pointSize: 14; text: modelData.l
                           color: root.eye === root.eyeValue(modelData.x, modelData.y) ? "white" : "black" }
                    MouseArea { anchors.fill: parent; onClicked: root.placeEye(modelData.x, modelData.y) }
                }
            }
        }
        Rectangle {
            id: page
            anchors.horizontalCenter: parent.horizontalCenter
            width: 600
            height: width * 1872 / 1404
            color: "white"
            border.color: "black"; border.width: 3
            readonly property real s: width / 1404      // page px -> canvas px
            readonly property real e: 56 * s            // eye size (as in the qmd)
            readonly property real m: 18 * s            // edge margin (as in the qmd)
            Rectangle {
                width: page.e; height: page.e; radius: page.e / 2
                x: page.m + root.eyeF[0] * (page.width - page.e - 2 * page.m)
                y: page.m + root.eyeF[1] * (page.height - page.e - 2 * page.m)
                color: "white"; border.color: "black"; border.width: 3
                Rectangle { anchors.centerIn: parent; width: parent.width / 3; height: width; radius: width / 2; color: "black" }
            }
            MouseArea {
                id: pageTap
                anchors.fill: parent
                onClicked: root.placeEye((pageTap.mouseX - page.m - page.e / 2) / (page.width - page.e - 2 * page.m),
                                         (pageTap.mouseY - page.m - page.e / 2) / (page.height - page.e - 2 * page.m))
            }
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "tap the page to place the eye · " + root.savedNote
            font.pointSize: 14
            color: "#666666"
        }
    }
    }
}
