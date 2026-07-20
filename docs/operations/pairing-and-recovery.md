# Pairing and recovery

## Start and pair

1. Start `moddebugpilot` in the logged-in console session.
2. Verify all paths and restrict the bind address or Windows Firewall rule.
3. Select **Start controller** in the native window.
4. Open the displayed HTTP URL from the intended LAN controller.
5. Select **Open pairing window** in the Agent.
6. Submit the eight-digit code and a recognizable browser name.
7. Refresh pending requests in the Agent and approve only the expected name.

A code expires after ten minutes, one valid request consumes it, and five wrong
guesses close the window. Approval is not persisted. Use a new code after a
rejection or listener restart.

The HTTP session exposes DLL, config, and screenshot bytes to the LAN path. Stop
if the network is shared or untrusted. A separately administered trusted TLS
reverse proxy is an optional deployment concern, not an application dependency.

## Prepare and run

1. Enter a Thunderstore Profile Code and safe Profile ID.
2. Select the locally built Debug DLL in the browser.
3. Import the profile, then review and edit the listed configs.
4. Install the verified bundle on the Agent.
5. Choose an instance name and unused debugger port, then launch.
6. Use the instance list to download screenshots or stop one tracked process.

Do not start normal Lethal Company while a debug instance is active. The Agent
refuses the first debug launch when an untracked game process is present.

## Normal shutdown

Select **Stop and restore** in the native Agent. It stops tracked processes,
restores Doorstop and normal saves, cancels watchers, and closes the listener.
After restoration completes, normal Steam launch needs no additional file work.

## Interrupted recovery

The next listener start recovers bootstrap and save journals before accepting
browser control. If it reports an invalid journal, missing normal backup,
unexpected backup, or archive collision:

1. Do not launch the game or delete either save directory.
2. Stop the Agent and confirm no Lethal Company process remains.
3. Copy the normal save directory, its `.moddebugpilot-normal` sibling, Agent
   data root, and isolation journals to separate backup media.
4. Inspect journal values and directory timestamps; do not guess which copy is
   canonical.
5. Repair only after identifying the original from copied evidence, then let
   Agent recovery finish.

Bootstrap restoration follows the same preserve-first rule.
