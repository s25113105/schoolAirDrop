import time
import os
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__)
app.config['SECRET_KEY'] = 'campusdrop_secret'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# 格式: { uid: { 'name': '暱稱', 'sid': 'socket_id', 'room': '101' } }
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
    user_room = None
    
    for uid, info in online_users.items():
        if info['sid'] == request.sid:
            disconnected_uid = uid
            user_room = info['room']
            break
    
    if disconnected_uid:
        del online_users[disconnected_uid]
        print(f"❌ 使用者離開: {disconnected_uid}")
        # 只廣播給同房間的人
        if user_room:
            broadcast_user_list(user_room)

@socketio.on('join')
def handle_join(data):
    uid = data.get('uid')
    name = data.get('name', '無名氏')
    # 🔥 關鍵：從前端取得使用者輸入的「房號」，預設為 'Lobby'
    room_id = data.get('room_id', 'Lobby') 
    
    if uid:
        # 如果使用者原本就在別的房間，這裡可以做切換邏輯 (Demo 簡單起見，直接覆蓋)
        online_users[uid] = {
            'name': name,
            'sid': request.sid,
            'room': room_id
        }
        
        # 加入 Socket.IO 房間
        join_room(room_id) 
        join_room(request.sid) # 個人房間 (信令用)

        print(f"✅ 使用者加入: {name} | 房號: {room_id}")
        
        # 只更新該房間的名單
        broadcast_user_list(room_id)

def broadcast_user_list(target_room):
    """只把名單發給同房間的人"""
    same_room_users = [
        {'uid': uid, 'name': info['name']} 
        for uid, info in online_users.items() 
        if info['room'] == target_room
    ]
    emit('update_user_list', same_room_users, room=target_room)

# --- P2P 信令轉發 ---
@socketio.on('p2p_signal')
def handle_p2p_signal(data):
    target_uid = data.get('target_uid')
    if target_uid in online_users:
        target_sid = online_users[target_uid]['sid']
        emit('p2p_signal', data, room=target_sid)

# --- 聊天轉發 ---
@socketio.on('group_chat')
def handle_group_chat(data):
    sender_uid = data.get('sender_uid')
    sender_info = online_users.get(sender_uid)
    
    if sender_info:
        room_id = sender_info['room']
        data['sender_name'] = sender_info['name']
        data['timestamp'] = time.time()
        
        # 只傳給同房間
        emit('group_chat', data, room=room_id, include_self=False)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)