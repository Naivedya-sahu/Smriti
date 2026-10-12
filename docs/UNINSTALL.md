# Uninstall — per box

Nothing here auto-deletes. Every step is a command YOU run. Order is free;
each box is independent.

## Daemon box (laptop Windows / Pi / any Linux)

Linux:
```sh
systemctl --user disable --now smriti-scribe 2>/dev/null   # if scribe here
sudo systemctl disable --now smriti-monke@$USER 2>/dev/null
sudo rm /etc/systemd/system/smriti-monke@.service
rm -rf ~/smriti                    # repo + venv (sessions/ exports live here — copy out first!)
rm -rf ~/.config/smriti            # env (keys, token), floor, blocks.log
rm -rf ~/.TinyTeX                  # only if nothing else uses it
rm ~/.ssh/id_ed25519_rm2*          # tablet key; also delete the Host rm2 block in ~/.ssh/config
```

Windows (PowerShell):
```powershell
Unregister-ScheduledTask -TaskName smriti-scribe -Confirm:$false  # if scribe here
Remove-Item -Recurse -Force $HOME\smriti          # copy sessions\ out first!
# keys/token were set with setx — clear them:
'GEMINI_API_KEY','OPENROUTER_API_KEY','SMRITI_CONTROL_TOKEN' |
  ForEach-Object { [Environment]::SetEnvironmentVariable($_, $null, 'User') }
```

Claude CLI + its login are shared with your other Claude Code use — remove
separately only if you really mean it (`~/.claude`, `~/.local/bin/claude`).

## Scribe-only GPU box

```powershell
Unregister-ScheduledTask -TaskName smriti-scribe -Confirm:$false   # Windows
Remove-Item -Recurse -Force $HOME\smriti
Remove-Item -Recurse -Force $HOME\.cache\huggingface   # TrOCR model cache
```
Linux: `systemctl --user disable --now smriti-scribe`, then delete
`~/.config/systemd/user/smriti-scribe.service`, `~/smriti`,
`~/.cache/huggingface`.

## Tablet (RM2)

```sh
ssh rm2
vellum del smriti                       # ink engine, fonts, lamp
systemctl disable --now smriti-eye-watch
rm /etc/systemd/system/smriti-eye-watch.service
rm /home/root/.vellum/bin/smriti-eye /home/root/.vellum/bin/smriti-eye-watch
rm -rf /home/root/.smriti /home/root/.config/smriti-eye
rm -rf /home/root/xovi/exthome/appload/smriti                       # toolbox app
rm /home/root/xovi/exthome/qt-resource-rebuilder/smriti-eye.qmd     # floating eye
systemctl restart xochitl
# optional, shared with other tools — remove only if unused elsewhere:
#   goMarkableStream:  systemctl disable --now goMarkableStream; rm /home/root/gomarkablestream
#   tailscale, xovi, vellum itself
```

Your notebooks and their ink are never touched by any of this.
