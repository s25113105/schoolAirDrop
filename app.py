import time
import os
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room
# 雲端版不需要 mDNS (zeroconf)，我們改用 Socket 追蹤連線

# --- 1. 基礎設定 ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'campusdrop_secret'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- 2. 線上使用者管理 ---
# 格式: { uid: { 'name': '暱稱', 'sid': 'socket_id' } }
online_users = {}

@app.route('/')
def index():
    return render_template('index.html')

# --- 3. SocketIO 事件處理 (核心) ---

@socketio.on('connect')
def handle_connect():
    print(f"🔗 新連線: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    """當使用者斷線時，從名單移除並廣播更新"""
    # 找出斷線的是哪個 UID
    disconnected_uid = None
    for uid, info in online_users.items():
        if info['sid'] == request.sid:
            disconnected_uid = uid
            break
    
    if disconnected_uid:
        del online_users[disconnected_uid]
        print(f"❌ 使用者離開: {disconnected_uid}")
        broadcast_user_list()

@socketio.on('join')
def handle_join(data):
    """使用者上線登入 (包含 UID 和 名稱)"""
    uid = data.get('uid')
    name = data.get('name', '無名氏')
    
    if uid:
        # 記錄使用者資訊
        online_users[uid] = {
            'name': name,
            'sid': request.sid
        }
        join_room('campus_chat')
        print(f"✅ 使用者加入: {name} (UID: {uid})")
        
        # 廣播最新的使用者列表給所有人
        broadcast_user_list()

def broadcast_user_list():
    """將目前的線上名單整理後發送給所有人"""
    # 轉換成陣列格式發送
    user_list = [{'uid': uid, 'name': info['name']} for uid, info in online_users.items()]
    emit('update_user_list', user_list, broadcast=True)

# --- 4. WebRTC 信令轉發 ---
@socketio.on('p2p_signal')
def handle_p2p_signal(data):
    target_uid = data.get('target_uid')
    sender_uid = data.get('sender_uid') # 前端傳來的發送者

    if target_uid in online_users:
        target_sid = online_users[target_uid]['sid']
        # 轉發給目標
        emit('p2p_signal', data, room=target_sid)

# --- 5. 聊天廣播 ---
@socketio.on('group_chat')
def handle_group_chat(data):
    # 加上時間戳記
    data['timestamp'] = time.time()
    emit('group_chat', data, room='campus_chat', include_self=False)

# --- 6. 啟動 ---
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)