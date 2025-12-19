import time
import os
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__)
app.config['SECRET_KEY'] = 'campusdrop_secret'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# 格式改變：我們需要多記住使用者的 IP
# { uid: { 'name': '暱稱', 'sid': 'socket_id', 'ip': '1.2.3.4' } }
online_users = {}

def get_real_ip():
    """
    取得使用者的真實 IP。
    在 Render/Heroku 等雲端環境，IP 會藏在 X-Forwarded-For 表頭裡。
    """
    if request.headers.getlist("X-Forwarded-For"):
        return request.headers.getlist("X-Forwarded-For")[0]
    return request.remote_addr

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    ip = get_real_ip()
    print(f"🔗 新連線: {request.sid} 來自 IP: {ip}")

@socketio.on('disconnect')
def handle_disconnect():
    disconnected_uid = None
    user_ip = None
    
    # 尋找斷線的使用者
    for uid, info in online_users.items():
        if info['sid'] == request.sid:
            disconnected_uid = uid
            user_ip = info['ip']
            break
    
    if disconnected_uid:
        del online_users[disconnected_uid]
        print(f"❌ 使用者離開: {disconnected_uid}")
        # 只廣播給同一個 IP 的房間
        if user_ip:
            broadcast_user_list_to_ip(user_ip)

@socketio.on('join')
def handle_join(data):
    uid = data.get('uid')
    name = data.get('name', '無名氏')
    ip = get_real_ip() # 抓取連線者的 IP
    
    if uid:
        # 1. 記錄使用者資訊 (包含 IP)
        online_users[uid] = {
            'name': name,
            'sid': request.sid,
            'ip': ip
        }
        
        # 2. 讓這個使用者加入「IP 專屬房間」
        # 這樣我們等一下廣播時，就可以只傳給這個房間的人
        join_room(ip) 
        
        # 3. 也加入個人的 socket 房間 (為了 P2P 信令)
        join_room(request.sid)

        print(f"✅ 使用者加入: {name} (IP: {ip})")
        
        # 4. 只更新「該 IP 房間」的名單
        broadcast_user_list_to_ip(ip)

def broadcast_user_list_to_ip(target_ip):
    """
    過濾名單：只取出 IP 相同的使用者，發送給該 IP 的房間
    """
    # 篩選：只找 IP 一樣的人
    same_network_users = [
        {'uid': uid, 'name': info['name']} 
        for uid, info in online_users.items() 
        if info['ip'] == target_ip
    ]
    
    # 發送：只傳給位於 'target_ip' 這個房間的人
    emit('update_user_list', same_network_users, room=target_ip)

# --- 信令轉發 (不變，但加上安全檢查) ---
@socketio.on('p2p_signal')
def handle_p2p_signal(data):
    target_uid = data.get('target_uid')
    sender_uid = data.get('sender_uid')
    
    # 安全檢查：確認目標存在，且雙方在同一個 IP 網路下 (可選)
    target_info = online_users.get(target_uid)
    sender_info = online_users.get(sender_uid)

    if target_info and sender_info:
        # 如果你想強制一定要同網域才能連，把下面這行註解打開：
        # if target_info['ip'] != sender_info['ip']: return 
        
        target_sid = target_info['sid']
        emit('p2p_signal', data, room=target_sid)

# --- 聊天轉發 (修改：只傳給同 IP 的人) ---
@socketio.on('group_chat')
def handle_group_chat(data):
    sender_uid = data.get('sender_uid')
    sender_info = online_users.get(sender_uid)
    
    if sender_info:
        ip = sender_info['ip']
        sender_name = sender_info['name']
        
        data['sender_name'] = sender_name
        data['timestamp'] = time.time()
        
        # 關鍵修改：只廣播給同一個 IP 房間的人
        emit('group_chat', data, room=ip, include_self=False)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)