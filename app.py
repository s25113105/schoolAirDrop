# 檔案: app.py
import socket
import time
import json
from uuid import uuid4
from threading import Thread
import os

# 導入 Flask, SocketIO, zeroconf
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf, ServiceStateChange

# --- 1. 基礎設定與變數 ---
app = Flask(__name__)
# 設置 Secret Key (用於 SocketIO 和 Session 安全)
app.config['SECRET_KEY'] = 'your_campusdrop_secret_key_888' 
# 初始化 SocketIO，允許跨域連接
socketio = SocketIO(app, cors_allowed_origins="*")

# 全局變數
my_uuid = str(uuid4())[:8]
my_name = "未設置名稱"
my_ip = ""
peers = {}  # 儲存 mDNS 發現的鄰近節點資訊: {uid: {'name': name, 'ip': ip}}
zc = None

# --- 2. 網路工具函數 ---
def get_ip():
    """獲取本機 IP 位址"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80)) 
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        s.close()

# --- 3. Zeroconf (mDNS) 發現與廣播邏輯 ---

def handler(zc, type, name, state_change):
    """mDNS 服務狀態改變時的處理器"""
    global peers
    
    # 僅處理服務添加事件
    if state_change == ServiceStateChange.Added:
        info = zc.get_service_info(type, name)
        if info and info.addresses:
            ip = socket.inet_ntoa(info.addresses[0]) 
            try:
                properties = dict(info.properties)
                # 解析屬性中的 uid|name
                uid, name_str = properties.get(b'n', b'|').decode().split('|', 1) 
                
                if uid != my_uuid: # 排除自己
                    peers[uid] = {"name": name_str, "ip": ip}
                    print(f"✅ 發現節點: {name_str} ({ip})")
            except Exception:
                pass

    # 處理服務移除事件
    elif state_change == ServiceStateChange.Removed:
        print(f"❌ 節點離線: {name}")
        # (這裡應增加邏輯，根據 name 查找並移除 peers 中的 entry)

def broadcast_service():
    """在獨立線程中運行 mDNS 廣播與發現"""
    global zc, my_ip, my_name
    my_ip = get_ip()
    
    # 確保只初始化一次 Zeroconf
    if zc is None:
        zc = Zeroconf()
        ServiceBrowser(zc, "_campusdrop._tcp.local.", handlers=[handler])
    
    # 註冊服務（持續廣播自己的存在）
    info = ServiceInfo(
        "_campusdrop._tcp.local.",
        f"{my_uuid}.CampusDrop._campusdrop._tcp.local.",
        port=5000, 
        addresses=[socket.inet_aton(my_ip)],
        properties={b'n': f"{my_uuid}|{my_name}".encode()}
    )
    
    try:
        zc.unregister_service(info) # 註銷舊服務
    except Exception:
        pass
        
    time.sleep(0.3)
    zc.register_service(info)
    print(f"📢 服務廣播中: {my_name} @ {my_ip}")

# --- 4. Flask 路由 (API) ---

@app.route('/')
def index():
    """主頁面，渲染前端 HTML"""
    return render_template('index.html', my_name=my_name, my_uuid=my_uuid)

@app.route('/setname', methods=['POST'])
def setname():
    """處理前端設定用戶名稱的請求"""
    global my_name
    data = request.get_json()
    new_name = data.get('name', '未設置名稱')
    
    if new_name != my_name:
        my_name = new_name
        # 在獨立線程中重新廣播服務以更新名稱
        Thread(target=broadcast_service, daemon=True).start()
        
    return jsonify({"status": "ok", "name": my_name})

@app.route('/peers')
def api_peers():
    """返回所有 mDNS 發現的鄰近節點列表"""
    peer_list = [{'name': v['name'], 'ip': v['ip'], 'uid': k} for k, v in peers.items()]
    return jsonify(peer_list)

# --- 5. SocketIO 處理 (P2P 信令核心) ---

class ConnectionManager:
    """管理 WebSocket 連線和 UID 映射"""
    def __init__(self):
        self.sid_to_uid = {}
    
    def get_uid_by_sid(self, sid):
        return self.sid_to_uid.get(sid)

manager = ConnectionManager()

@socketio.on('connect')
def handle_connect():
    """處理新的 WebSocket 連線"""
    print(f"🔗 新的 WebSocket 連線: {request.sid}")

@socketio.on('set_uid')
def handle_set_uid(data):
    """前端發送自己的 UID"""
    uid = data.get('uid')
    if uid:
        manager.sid_to_uid[request.sid] = uid
        join_room('campus_chat')
        print(f"✅ UID 設置: {uid}")

@socketio.on('p2p_signal')
def handle_p2p_signal(data):
    """處理 WebRTC 信令轉發 (SDP/ICE)"""
    target_uid = data.get('target_uid')
    sender_uid = manager.get_uid_by_sid(request.sid)

    if target_uid and sender_uid:
        # 查找目標的 sid
        target_sid = next((sid for sid, uid in manager.sid_to_uid.items() if uid == target_uid), None)
        
        if target_sid:
            data['sender_uid'] = sender_uid
            emit('p2p_signal', data, room=target_sid) # 轉發信令

@socketio.on('group_chat')
def handle_group_chat(data):
    """處理群組聊天訊息廣播"""
    sender_uid = manager.get_uid_by_sid(request.sid)
    data['sender_uid'] = sender_uid
    data['timestamp'] = time.time()
    
    # 廣播給 'campus_chat' 房間內的所有人 (除了發送者)
    emit('group_chat', data, room='campus_chat', include_self=False)


# --- 6. 啟動程序 ---
if __name__ == '__main__':
    print("====================================")
    print(f"🌐 啟動 CampusDrop 專案，您的 UID: {my_uuid}")
    
    # 啟動 Zeroconf 服務 (必須在獨立線程中)
    Thread(target=broadcast_service, daemon=True).start()
    
    # 使用 socketio.run 啟動 Flask 應用程式
    # 注意：在雲端部署時， host 應為 '0.0.0.0'
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)