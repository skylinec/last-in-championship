import logging
from datetime import datetime
from flask import session, request
from flask_socketio import SocketIO, emit, join_room, leave_room

# Create SocketIO instance at module level
socketio = SocketIO()

# Store WebSocket connections
websocket_connections = {}

@socketio.on('connect')
def on_connect():
    try:
        current_user = session.get('user')
        if not current_user:
            logging.warning("Unauthenticated connection attempt")
            return False

        logging.info(f"Client connected (sid={request.sid}) - User: {current_user}")
        websocket_connections[request.sid] = {
            'user': current_user,
            'connected_at': datetime.now(),
            'reconnect_count': 0
        }
        emit('connected', {'status': 'ok', 'sid': request.sid})
        return True
    except Exception as e:
        logging.error(f"Connection error: {str(e)}")
        return False

@socketio.on('disconnect')
def on_disconnect():
    try:
        conn_info = websocket_connections.pop(request.sid, None)
        if conn_info:
            logging.info(
                f"Client disconnected (sid={request.sid}) - "
                f"User: {conn_info['user']}, "
                f"Duration: {datetime.now() - conn_info['connected_at']}"
            )
    except Exception as e:
        logging.error(f"Disconnect error: {str(e)}")

@socketio.on('join_game')
def handle_join_game(data):
    """Handle game room joining"""
    game_id = data.get('game_id')
    if game_id:
        join_room(f"game_{game_id}")
        emit('joined', {'game_id': game_id})

@socketio.on('leave_game')
def handle_leave_game(data):
    """Handle game room leaving"""
    game_id = data.get('game_id')
    if game_id:
        leave_room(f"game_{game_id}")

def notify_game_update(game_id, game_state, winner=None):
    """Broadcast game updates to all players"""
    emit('game_update', {
        'state': game_state,
        'winner': winner
    }, room=f'game_{game_id}')

# Error handlers
@socketio.on_error
def error_handler(e):
    logging.error(f"SocketIO error: {str(e)}")
    return {'error': str(e)}

@socketio.on_error_default
def default_error_handler(e):
    logging.error(f"Unhandled SocketIO error: {str(e)}")
    return {'error': str(e)}
