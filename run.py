"""Startskript für den TradeBot"""
import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

import config
from app import create_app, socketio
from services.watchdog import sd_notify, start_watchdog

if __name__ == '__main__':
    app = create_app()
    sd_notify('READY=1')
    start_watchdog(config.PORT)
    # allow_unsafe_werkzeug: im threading-Modus dient Werkzeug als Server;
    # für das interne Dashboard ist das ausreichend.
    socketio.run(app, host='0.0.0.0', port=config.PORT, debug=False,
                 use_reloader=False, allow_unsafe_werkzeug=True)
