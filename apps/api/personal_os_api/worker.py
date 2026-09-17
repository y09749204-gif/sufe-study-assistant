"""Independent periodic sync and durable recording queue; no desktop executor."""
import json,time,threading
from datetime import datetime,timezone
from .config import get_settings,data_root
from .db import SessionLocal


def queue_loop():
    from .kzkt_queue import tick
    while True:
        try:tick()
        except Exception:
            pass # Per-job failures are persisted by the queue; never print raw content.
        time.sleep(3)


def main():
    threading.Thread(target=queue_loop,daemon=True).start()
    last_canvas=last_wecom=last_discovery=0
    while True:
        cfg=get_settings();now=time.monotonic();state={}
        if cfg.values.get('term_id'):
            if cfg.canvas_expected_user_id and now-last_canvas>6*3600:
                try:
                    from .connections import start_browser
                    start_browser('canvas','sync');state['canvas']='started'
                except Exception:state['canvas']='failed'
                last_canvas=now
            if cfg.values.get('wecom') and now-last_wecom>300:
                try:
                    from .academics import refresh_wecom_academics
                    with SessionLocal() as db:refresh_wecom_academics(db)
                    state['wecom']='ready'
                except Exception:state['wecom']='failed'
                last_wecom=now
            if now-last_discovery>7200:
                try:
                    from .kzkt_queue import enqueue
                    with SessionLocal() as db:enqueue(db)
                    state['kzkt']='queued'
                except Exception:state['kzkt']='failed'
                last_discovery=now
        data_root().mkdir(parents=True,exist_ok=True)
        (data_root()/'worker.json').write_text(json.dumps({'checked_at':datetime.now(timezone.utc).isoformat(),**state}),'utf-8')
        time.sleep(30)


if __name__=='__main__':main()
