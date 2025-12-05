import socket
import time
import json
import os
from uuid import uuid4
from threading import Thread
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
# 引入 mDNS 相關套件，並做錯誤處理準備
try:
    from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf, ServiceStateChange
    ZEROCONF_AVAILABLE = True
except ImportError:
    ZEROCONF_AVAILABLE = False
    print("⚠️ 未安裝 zeroconf，將略過 mDNS 功能")

# --- 1. 基礎設定與變數 ---
app = Flask(__name__)
# 設定 Secret Key (用於 SocketIO 和 Session 安全)
app.config['SECRET_KEY'] = 'your_campusdrop_secret_key_888'

# 初始化 SocketIO，允許跨域連接 (cors_allowed_origins="*")
# 並且指定 async_mode='eventlet' 讓雲端部署更穩定
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

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
        # 嘗試連線到 Google DNS (不會真的送出封包) 來判斷對外 IP
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        s.close()

# --- 3. Zeroconf (mDNS) 發現與廣播邏輯 ---

def on_service_state_change(zeroconf, service_type, name, state_change):
    """mDNS 服務狀態改變時的處理器"""
    global peers
    
    if not ZEROCONF_AVAILABLE:
        return

    try:
        if state_change == ServiceStateChange.Added:
            info = zeroconf.get_service_info(service_type, name)
            if info and info.addresses:
                # 轉換 IP
                ip = socket.inet_ntoa(info.addresses[0])
                
                # 解析屬性
                properties = dict(info.properties)
                # 解析屬性中的 uid|name (b'n' 是 key)
                raw_prop = properties.get(b'n', b'|')
                decoded_prop = raw_prop.decode('utf-8', errors='ignore')
                
                if '|' in decoded_prop:
                    uid, name_str = decoded_prop.split('|', 1)
                else:
                    uid, name_str = "unknown", decoded_prop

                if uid != my_uuid:  # 排除自己
                    peers[uid] = {"name": name_str, "ip": ip}
                    print(f"✅ [mDNS] 發現節點: {name_str} ({ip})")

        elif state_change == ServiceStateChange.Removed:
            # 簡單處理：從名稱中移除 (實際應用可能需要更嚴謹的 mapping)
            print(f"❌ [mDNS] 節點離線: {name}")
            # 這裡暫時無法直接從 name 反查 uid 移除 peers，
            # 為了 demo 穩定性，暫時保留在列表或可實作定時清除。
    except Exception as e:
        print(f"⚠️ mDNS Handler Error: {e}")

def broadcast_service():
    """在獨立線程中運行 mDNS 廣播與發現"""
    global zc, my_ip, my_name
    
    if not ZEROCONF_AVAILABLE:
        return

    my_ip = get_ip()
    
    # 確保只初始化一次 Zeroconf
    if zc is None:
        try:
            zc = Zeroconf()
            # 啟動瀏覽器尋聽服務
            ServiceBrowser(zc, "_campusdrop._tcp.local.", handlers=[on_service_state_change])
            print("📡 mDNS 監聽服務已啟動")
        except Exception as e:
            print(f"⚠️ 無法啟動 mDNS (可能是雲端環境): {e}")
            return # 如果啟動失敗（例如在 Render），直接結束這個函數，不要讓程式崩潰

    # 註冊服務 (持續廣播自己的存在)
    desc = {'n': f"{my_uuid}|{my_name}".encode('utf-8')}
    
    info = ServiceInfo(
        "_campusdrop._tcp.local.",
        f"{my_uuid}.CampusDrop._campusdrop._tcp.local.",
        addresses=[socket.inet_aton(my_ip)],
        port=5000,
        properties=desc,
        server=f"{my_uuid}.local."
    )

    try:
        # 嘗試註銷舊的（如果有）
        zc.unregister_service(info)
        time.sleep(0.3) # 等待一下
        # 註冊新的
        zc.register_service(info)
        print(f"📣 服務廣播中: {my_name} @ {my_ip} (UID: {my_uuid})")
    except Exception as e:
        print(f"⚠️ mDNS 廣播失敗 (忽略): {e}")

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
        # 在獨立線程中重新廣播服務以更新名稱 (僅在本機有效)
        if ZEROCONF_AVAILABLE and zc:
            Thread(target=broadcast_service, daemon=True).start()

    return jsonify({"status": "ok", "name": my_name})

@app.route('/peers')
def api_peers():
    """返回所有 mDNS 發現的鄰近節點列表"""
    # 將 peers 字典轉換為列表格式回傳
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
            # 轉發信令給目標
            emit('p2p_signal', data, room=target_sid)
            # print(f"🔀 轉發信令: {sender_uid} -> {target_uid} ({data.get('type')})")

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
    print("========================================")
    print(f"🌐 啟動 CampusDrop 專案，您的 UID: {my_uuid}")
    
    # 啟動 Zeroconf 服務 (必須在獨立線程中)
    # 在雲端環境中，這行會被 try-except 捕獲而不會讓程式崩潰
    if ZEROCONF_AVAILABLE:
        Thread(target=broadcast_service, daemon=True).start()

    # 使用 socketio.run 啟動 Flask 應用程式
    # 注意：在雲端部署時，host 應為 '0.0.0.0'
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)