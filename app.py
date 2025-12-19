import time
import os
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
app.config['SECRET_KEY'] = 'campusdrop_secret'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# 格式: { uid: { 'name': '暱稱', 'sid': 'socket_id', 'net_id': '...' } }
online_users = {}

def get_network_id():
    """
    🔥 聰明 IP 判斷法：只取 IP 的前三段。
    例如: 203.1.1.5 -> 203.1.1
    這樣就算學校電腦尾碼不同，只要在同一個網段，就能互相看到。
    """
    ip = request.remote_addr
    # 如果有經過代理伺服器 (Render)，抓取真實 IP
    if request.headers.getlist("X-Forwarded-For"):
        ip = request.headers.getlist("X-Forwarded-For")[0].split(',')[0].strip()
    
    # 嘗試將 IPv4 切割 (1.2.3.4 -> [1, 2, 3, 4])
    parts = ip.split('.')
    if len(parts) == 4:
        # 只取前 3 段當作「房間號碼」
        return f"{parts[0]}.{parts[1]}.{parts[2]}"
    
    # 如果是 IPv6 或其他格式，就直接用原 IP
    return ip

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    net_id = get_network_id()
    print(f"🔗 新連線: {request.sid} | 網段: {net_id}")

@socketio.on('disconnect')
def handle_disconnect():
    disconnected_uid = None
    user_net_id = None
    
    for uid, info in online_users.items():
        if info['sid'] == request.sid:
            disconnected_uid = uid
            user_net_id = info['net_id']
            break
    
    if disconnected_uid:
        del online_users[disconnected_uid]
        print(f"❌ 使用者離開: {disconnected_uid}")
        # 只廣播給同網段的人
        if user_net_id:
            broadcast_user_list_to_network(user_net_id)

@socketio.on('join')
def handle_join(data):
    uid = data.get('uid')
    name = data.get('name', '無名氏')
    
    # 🔥 關鍵：取得「網段 ID」而不是完整 IP
    net_id = get_network_id()
    
    if uid:
        online_users[uid] = {
            'name': name,
            'sid': request.sid,
            'net_id': net_id
        }
        
        # 加入「網段專屬房間」
        join_room(net_id) 
        join_room(request.sid) # 個人房間

        print(f"✅ 使用者加入: {name} (網段: {net_id})")
        
        # 只更新該網段的名單
        broadcast_user_list_to_network(net_id)

def broadcast_user_list_to_network(target_net_id):
    """只把名單發給同網段的人"""
    same_network_users = [
        {'uid': uid, 'name': info['name']} 
        for uid, info in online_users.items() 
        if info['net_id'] == target_net_id
    ]
    emit('update_user_list', same_network_users, room=target_net_id)

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
        net_id = sender_info['net_id']
        data['sender_name'] = sender_info['name']
        data['timestamp'] = time.time()
        
        # 只傳給同網段
        emit('group_chat', data, room=net_id, include_self=False)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)