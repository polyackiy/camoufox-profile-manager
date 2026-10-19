# Windows 11 protected VM trial — 18 October 2026

This is limited acceptance of the published **v0.6.0-rc.8**, source
`1df2b3daa3da93f7077e347ba7133eb5b7378d11`. It does not clear #61, #62 or #63.
The [Windows follow-up](desktop-acceptance-windows-2026-10-19.md) records the
rebuilt D09 fix, fuller data/session trials and Windows 10 evidence. Results
below describe this historical published installer, not the corrected package.

- Asset: `camoufox-pm-windows-x86_64-setup.exe`, 86,322,905 bytes.
- SHA-256: `e64fe3a166f3c371e18e40a648ca34f80071ef3a73e7c398f44d49d7e2aa9041`.
- Official Windows 11 Enterprise Evaluation 26H2 x64, build 26300.9457,
  QEMU/KVM, ordinary fresh `tester` account. Secure Boot, ready TPM 2.0 and UAC.
- Python/Node not separately installed. WebView2 already present: 155.0.4283.62.
- Defender 4.18.26090.9, signatures 1.461.88.0, Normal mode; real-time,
  behavior and IOAV protection enabled. No exclusions or protection changes.
- Exact asset downloaded inside the isolated guest; GitHub digest, sidecar and
  size matched. Internet Zone 3 recorded before launch. A completed Defender
  custom scan reported no detections. Final threat history was also empty.
- Installer and app are unsigned; publisher and timestamp are absent.
- Explorer opening showed SmartScreen. The human handled that decision and
  started setup; the normal per-user wizard then completed. The agent did not
  bypass the warning. The Zone.Identifier stream was absent after that human
  action; its prior presence is separate evidence.

The earlier unattended Shell Open attempt timed out without observable setup
execution. Its cause is not established. The manual continuation followed a
normal guest reboot on the same diagnostic disk, not another fresh baseline.
Old failed automated reports remain failures; there is no successful fresh
end-to-end one-command run yet. The revised local runner opens Explorer and
waits for operator installation, keeping Windows trust decisions with a human.

| Scenario | Result | Observation |
| --- | --- | --- |
| D01 | Pass | Exact asset, digest, size and Internet provenance recorded. |
| D02 | Partial | Ordinary-user install, registration, Start menu launch and bundled UI work; WebView2-absent/cancellation cases untested. |
| D03 | Partial | Native manager close interrupted download at about 46%; reopening reported idle/uninstalled, then retry installed Camoufox 156.0.1-beta.36. No partial download was reported ready. Error presentation was not fully exercised. |
| D04 | Partial | Real browser launch/relaunch, persistent synthetic cookie and stored fingerprint metadata passed through API; localStorage and site-visible identity/canvas fixture untested. |
| D05 | Partial | Native New Tab/Close Tab worked, retaining the original tab; site-visible identity comparison untested. |
| D06 | Partial | API backup/restore generated a distinct profile ID; complete restored native session fixture untested. |
| D07 | Partial | API Trash restore and refusal of unconfirmed permanent deletion passed; wrong-name/native permanent deletion untested. |
| D08 | Pass | Native manager close with browser running stopped its API and all 17 recorded original manager/browser/WebView/driver processes, identified by PID and creation time. |
| D09 | Defect | First API remained healthy; second process had no listener. Its native error window showed `[Errno 13] Permission denied`, matching the log traceback reading the lock byte, instead of the intended already-open message. Closing that error window is required before expecting its process to exit. |
| D10–D12 | Not tested | No complete update-preparation fixture or distinct later signed package. |
| D13 | Partial | Same-package reinstall preserved stopped database/key bytes; profile metadata and synthetic cookie remained usable. Encrypted proxy credentials untested. |
| D14 | Partial | Normal uninstaller removed app/registration and retained stopped database/key bytes; reinstall reused profiles and cookie. Whole manager fixture untested. |

The D09 fix uses file size metadata instead of reading a byte subject to a
Windows mandatory lock. A real competing-process regression failed with
`PermissionError` before the fix on Windows and passed afterward, including
reacquisition after release. This establishes the source lock error path,
not acceptance of a rebuilt packaged GUI.

Windows 10, WebView2-absent, full GUI/IME and manager fixtures, distinct signed
upgrades/rollback, physical cold canvas and independent human protocol review
remain open. Raw disks, profile databases, keys and complete desktop logs stay
private; only bounded synthetic observations are included in the local report.
