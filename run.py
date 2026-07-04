"""Startskript für den TradeBot"""
import config
from app import create_app, socketio

if __name__ == '__main__':
    app = create_app()
    socketio.run(app, host='0.0.0.0', port=config.PORT, debug=False, use_reloader=False)
