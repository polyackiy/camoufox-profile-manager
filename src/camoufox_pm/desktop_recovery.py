"""Local-only recovery window: no web server or application database is opened."""

from collections.abc import Callable
from threading import Lock

# All changing values enter textContent through the local bridge. No remote page
# is loaded, and no filesystem path/command is accepted from the UI.
RECOVERY_HTML = """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; img-src 'none'">
<title>Update recovery — Camoufox Profile Manager</title>
<style>
:root{color-scheme:light dark;font:16px/1.55 system-ui,sans-serif}body{margin:0;padding:32px;max-width:720px}h1{font-size:24px;line-height:1.25}p{max-width:65ch}button,input{font:inherit}button{min-height:44px;padding:8px 16px;margin:4px 8px 4px 0;border:1px solid #808080;border-radius:7px;cursor:pointer}button:disabled{opacity:.5;cursor:default}button:focus-visible,input:focus-visible{outline:3px solid #4187e8;outline-offset:3px}label{display:flex;gap:12px;align-items:center;min-height:44px}.error{color:light-dark(#a31515,#ffb4b4)}[hidden]{display:none!important}.panel{border:1px solid #808080;border-radius:8px;padding:16px;margin:20px 0}
</style>
<main><h1>Update recovery</h1><p>The manager has paused normal startup. Your profiles will remain closed until recovery finishes.</p>
<div class="panel" role="status" aria-live="polite"><p id="state">Reading recovery status…</p><p id="versions"></p><p id="checkpoint"></p><p id="reason"></p></div>
<p id="error" class="error" role="alert"></p>
<div id="confirmation" class="panel" hidden><p id="restore-effect">Restoring returns the manager, browser and data to the recovery point above. Changes made later will no longer be active. A copy of the newer state is retained.</p><label><input id="acknowledge" type="checkbox" aria-describedby="restore-effect">I understand that later changes will no longer be active.</label><button id="dismiss-restore" type="button">Cancel restore</button></div>
<div><button id="refresh" type="button">Refresh status</button><button id="resume" type="button" hidden>Retry interrupted update</button><button id="restore" type="button" hidden>Restore previous version</button><button id="cancel" type="button" hidden>Cancel preparation</button><button id="open" type="button" hidden>Open manager</button></div>
<p>Keep this window open while reviewing recovery. If the problem persists, consult the update recovery guide and the local recovery log.</p></main>
<script>
let current=null, busy=false, refreshing=false, restoreConfirmation=null;
const byId=id=>document.getElementById(id);
const labels={idle:'No update is in progress.',staged:'The verified update is ready to install.',handoff_requested:'Waiting for the manager to close…',downloading:'Downloading and verifying the update…',preparing:'Creating the recovery point…',prepared:'The recovery point is ready.',checkpoint_interrupted:'Recovery point creation was interrupted.',installing_browser:'Installing the paired browser…',browser_ready:'The paired browser is ready.',replacing:'Installing the manager update…',validating:'Checking the updated manager and browser…',rolling_back:'Restoring the previous version and data…',committed:'The update completed successfully.',rolled_back:'The previous version and data have been restored.',cancelled:'Update preparation was cancelled.',failed:'The update could not finish. Review the error and try again.'};
function error(message){byId('error').textContent=message||'';}
function timestamp(s){return s.checkpoint_timestamp&&Number.isFinite(new Date(s.checkpoint_timestamp).getTime())?s.checkpoint_timestamp:null;}
function rollbackId(s){return s.rollback_transaction_id||s.transaction_id||'';}
function confirmationMatches(){return restoreConfirmation&&current&&current.can_rollback&&restoreConfirmation.id===rollbackId(current)&&restoreConfirmation.timestamp===timestamp(current);}
function controls(){
  document.querySelectorAll('button').forEach(b=>b.disabled=busy);
  byId('restore').disabled=busy||!!restoreConfirmation&&(!confirmationMatches()||!byId('acknowledge').checked);
  byId('restore').textContent=restoreConfirmation?'Confirm restore':'Restore previous version';
  byId('confirmation').hidden=!restoreConfirmation;
  byId('acknowledge').disabled=busy||!confirmationMatches();
  document.querySelector('main').setAttribute('aria-busy',String(busy||refreshing));
}
function show(s){
  current=s;
  byId('state').textContent=labels[s.state]||'Recovery status unavailable. Refresh to try again.';
  byId('versions').textContent=s.target_version?'Manager '+s.target_version+' / Camoufox '+(s.browser_version||'—'):'';
  byId('checkpoint').textContent=timestamp(s)?'Recovery point: '+new Date(timestamp(s)).toLocaleString():'';
  byId('reason').textContent=s.disabled_reason||'';
  error(restoreConfirmation&&!confirmationMatches()?'The recovery state changed. Cancel this restore, refresh and review the recovery point again.':s.error);
  for(const [id,key] of [['resume','can_resume'],['restore','can_rollback'],['cancel','can_cancel']])byId(id).hidden=!s[key]||(id==='restore'&&(!timestamp(s)||!rollbackId(s)));
  byId('open').hidden=s.maintenance_active||s.request_pending||!['idle','cancelled','committed','rolled_back','staged','failed'].includes(s.state);
  controls();
}
async function refresh(){
  if(busy||refreshing)return;
  refreshing=true;controls();
  try{show(await pywebview.api.status());}catch(e){error(String(e));}
  finally{refreshing=false;controls();}
}
async function act(action){
  if(busy||!current)return;
  if(action==='rollback'&&!restoreConfirmation){
    if(!current.can_rollback||!timestamp(current)||!rollbackId(current))return;
    restoreConfirmation={id:rollbackId(current),timestamp:timestamp(current)};
    byId('acknowledge').checked=false;show(current);byId('acknowledge').focus();return;
  }
  if(action==='rollback'&&(!confirmationMatches()||!byId('acknowledge').checked)){
    error('Review and confirm the current recovery point before restoring.');return;
  }
  const id=action==='rollback'?restoreConfirmation.id:current.transaction_id||'';
  const time=action==='rollback'?restoreConfirmation.timestamp:null;
  busy=true;controls();error('');
  try{
    const result=await pywebview.api.action(action,id,time);
    restoreConfirmation=null;byId('acknowledge').checked=false;show(result);
  }catch(e){error(String(e));}
  finally{busy=false;controls();}
}
byId('acknowledge').onchange=controls;
byId('dismiss-restore').onclick=()=>{if(busy)return;restoreConfirmation=null;byId('acknowledge').checked=false;show(current);byId('restore').focus();};
byId('refresh').onclick=refresh;
byId('resume').onclick=()=>act('resume');
byId('restore').onclick=()=>act('rollback');
byId('cancel').onclick=()=>act('cancel');
byId('open').onclick=async()=>{
  if(busy)return;busy=true;controls();
  try{await pywebview.api.open_manager();}catch(e){error(String(e));}
  finally{busy=false;controls();}
};
window.addEventListener('pywebviewready',()=>{refresh();setInterval(refresh,2500);});
</script></html>"""


class RecoveryBridge:
    """Expose only fixed actions; backend revalidates every displayed capability."""

    def __init__(
        self, status: Callable[[], dict], dispatch: Callable[[str, str, str | None], dict]
    ):
        self._status = status
        self._dispatch = dispatch
        self._lock = Lock()
        self._window = None
        self._open_requested = False

    def status(self) -> dict:
        return self._status()

    def action(self, action: str, transaction_id: str, timestamp: str | None = None) -> dict:
        if action not in {"resume", "rollback", "cancel"}:
            raise ValueError("Unsupported recovery action")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A recovery action is already in progress")
        try:
            current = self._status()
            capability = {
                "resume": "can_resume",
                "rollback": "can_rollback",
                "cancel": "can_cancel",
            }[action]
            if not current.get(capability):
                raise ValueError("This action is no longer available; refresh recovery status")
            expected_id = (
                current.get("rollback_transaction_id")
                if action == "rollback"
                else current.get("transaction_id", "")
            )
            if transaction_id != (expected_id or current.get("transaction_id", "")):
                raise ValueError("Recovery transaction changed; refresh recovery status")
            if action == "rollback" and (
                not timestamp or timestamp != current.get("checkpoint_timestamp")
            ):
                raise ValueError("Confirm the current recovery timestamp before restoring")
            result = self._dispatch(action, transaction_id, timestamp)
            if action in {"resume", "rollback"} and self._window is not None:
                self._window.destroy()
            return result
        finally:
            self._lock.release()

    def open_manager(self) -> None:
        with self._lock:
            status = self._status()
            if (
                status.get("maintenance_active")
                or status.get("request_pending")
                or status.get("state")
                not in {"idle", "cancelled", "committed", "rolled_back", "staged", "failed"}
            ):
                raise ValueError("Finish recovery before opening the manager")
            self._open_requested = True
            if self._window is not None:
                self._window.destroy()


def show_recovery(
    status: Callable[[], dict], dispatch: Callable[[str, str, str | None], dict]
) -> bool:
    import webview

    bridge = RecoveryBridge(status, dispatch)
    bridge._window = webview.create_window(
        "Camoufox Profile Manager — Update recovery",
        html=RECOVERY_HTML,
        js_api=bridge,
        width=780,
        height=660,
        min_size=(520, 480),
    )
    if bridge._window is None:
        raise RuntimeError("The recovery window could not be created")
    webview.start()
    return bridge._open_requested
