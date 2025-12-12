import time
import os
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
app.config['SECRET_KEY'] = 'campusdrop_secret'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# 格式: { uid: { 'name': '暱稱', 'sid': 'socket_id' } }
online_users = {}

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    print(f"🔗 新連線: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    disconnected_uid = None
    for uid, info in online_users.items():
        if info['sid'] == request.sid:
            disconnected_uid = uid
            break
    if disconnected_uid:
        del online_users[disconnected_uid]
        broadcast_user_list()

@socketio.on('join')
def handle_join(data):
    uid = data.get('uid')
    name = data.get('name', '無名氏')
    if uid:
        online_users[uid] = {'name': name, 'sid': request.sid}
        join_room('campus_chat')
        broadcast_user_list()

def broadcast_user_list():
    user_list = [{'uid': uid, 'name': info['name']} for uid, info in online_users.items()]
    emit('update_user_list', user_list, broadcast=True)

@socketio.on('p2p_signal')
def handle_p2p_signal(data):
    target_uid = data.get('target_uid')
    if target_uid in online_users:
        target_sid = online_users[target_uid]['sid']
        emit('p2p_signal', data, room=target_sid)

# --- 修正重點：聊天時把名字帶進去 ---
@socketio.on('group_chat')
def handle_group_chat(data):
    sender_uid = data.get('sender_uid')
    user_info = online_users.get(sender_uid)
    
    # 如果找得到名字就用名字，找不到就用 UID
    sender_name = user_info['name'] if user_info else sender_uid
    
    data['sender_name'] = sender_name
    data['timestamp'] = time.time()
    
    emit('group_chat', data, room='campus_chat', include_self=False)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)