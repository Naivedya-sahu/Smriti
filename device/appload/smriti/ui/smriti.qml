import QtQuick 2.5
import QtQuick.Controls 2.5

// Smriti toolbox — AppLoad frontend-only app.
// Talks to the Monke daemon through files (QML XHR can read/write
// file:// URLs): writes start/stop into /home/root/.smriti/eye-cmd,
// reads daemon state from /home/root/.smriti/state. The
// smriti-eye-watch service bridges those files to the daemon over the
// tailnet.

Rectangle {
    id: root
    anchors.fill: parent
    color: "white"

    property bool watching: false
    property string lastSync: "never"

    signal close
    function unloading() {
    }

    function refresh() {
        var xhr = new XMLHttpRequest();
        xhr.open("GET", "file:///home/root/.smriti/state");
        xhr.onreadystatechange = function() {
            if (xhr.readyState === XMLHttpRequest.DONE) {
                root.watching = (xhr.responseText.indexOf("watching") === 0);
                root.lastSync = new Date().toLocaleTimeString();
            }
        };
        xhr.send();
    }

    function command(cmd) {
        var xhr = new XMLHttpRequest();
        xhr.open("PUT", "file:///home/root/.smriti/eye-cmd");
        xhr.send(cmd);
        root.watching = (cmd === "start");
    }

    Timer {
        interval: 3000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refresh()
    }

    Column {
        anchors.centerIn: parent
        spacing: 60

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Smriti"
            font.pointSize: 42
            font.bold: true
        }

        // the eye: tap to toggle the session
        Rectangle {
            id: eye
            anchors.horizontalCenter: parent.horizontalCenter
            width: 220
            height: 220
            radius: 110
            color: "white"
            border.color: "black"
            border.width: 8

            Rectangle {           // pupil — session on
                anchors.centerIn: parent
                width: 70
                height: 70
                radius: 35
                color: "black"
                visible: root.watching
            }
            Rectangle {           // dash — idle
                anchors.centerIn: parent
                width: 100
                height: 14
                color: "black"
                visible: !root.watching
            }

            MouseArea {
                anchors.fill: parent
                onClicked: root.command(root.watching ? "stop" : "start")
            }
        }

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: root.watching
                  ? "watching — write on the page, pause, Monke replies"
                  : "idle — tap the eye to start a session"
            font.pointSize: 20
            width: root.width - 120
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
        }

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "last sync: " + root.lastSync
            font.pointSize: 14
            color: "#666666"
        }
    }
}
