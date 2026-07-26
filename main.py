#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import time
import logging
import requests
import hmac
import hashlib
import base64
from datetime import datetime
from pathlib import Path

# 配置日志
log_dir = Path('logs')
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_dir / 'bili_notifier.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 常量
BILIBILI_ROOM_INIT_API = "https://api.live.bilibili.com/room/v1/Room/room_init"
BILIBILI_ROOM_INFO_API = "https://api.live.bilibili.com/room/v1/Room/get_info"

# 用于记录已发送通知的房间
live_status_cache = {}


def load_config():
    """加载配置文件"""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error("config.json not found!")
        raise
    except json.JSONDecodeError:
        logger.error("config.json is not valid JSON!")
        raise


def get_dingtalk_signature(secret, timestamp):
    """生成钉钉加签"""
    message = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        secret.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).digest()
    signature = base64.b64encode(hmac_code).decode('utf-8')
    return signature


def send_dingtalk_notification(config, room_id, anchor_name, live_status, room_info):
    """发送钉钉通知"""
    try:
        webhook = config['dingtalk']['webhook']
        secret = config['dingtalk']['secret']
        
        # 生成时间戳和签名
        timestamp = str(int(time.time() * 1000))
        signature = get_dingtalk_signature(secret, timestamp)
        
        # 构建消息内容
        if live_status == 1:
            title = f"🔴 {anchor_name} 开播了！"
            status_text = "直播中"
            color = "red"
        elif live_status == 2:
            title = f"⏸️  {anchor_name} 正在轮播"
            status_text = "轮播中"
            color = "orange"
        else:
            return False
        
        # 获取房间标题
        room_title = room_info.get('title', '未获取到标题') if room_info else '未获取到标题'
        online_count = room_info.get('online', 0) if room_info else 0
        
        # 构建钉钉 Markdown 消息
        content = f"""# {title}

**房间号**: {room_id}
**主播**: {anchor_name}
**标题**: {room_title}
**状态**: {status_text}
**在线人数**: {online_count}

[点击进入直播间](https://live.bilibili.com/{room_id})
"""
        
        # 构建请求参数
        params = {
            'access_token': webhook.split('access_token=')[1],
            'timestamp': timestamp,
            'sign': signature
        }
        
        message_data = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": content
            }
        }
        
        # 发送请求
        response = requests.post(
            webhook,
            json=message_data,
            params=params,
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('errcode') == 0:
                logger.info(f"✅ DingTalk notification sent for room {room_id} ({anchor_name})")
                return True
            else:
                logger.error(f"❌ DingTalk error: {result.get('errmsg')}")
                return False
        else:
            logger.error(f"❌ HTTP error {response.status_code}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error sending DingTalk notification: {e}")
        return False


def get_room_info(room_id):
    """获取房间详情"""
    try:
        response = requests.get(
            BILIBILI_ROOM_INFO_API,
            params={'room_id': room_id},
            timeout=10,
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get('code') == 0:
                return data.get('data', {})
        
        return None
    except Exception as e:
        logger.error(f"Error getting room info for {room_id}: {e}")
        return None


def check_live_status(room_id):
    """检查直播状态"""
    try:
        response = requests.get(
            BILIBILI_ROOM_INIT_API,
            params={'id': room_id},
            timeout=10,
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get('code') == 0:
                room_data = data.get('data', {})
                return {
                    'room_id': room_data.get('room_id'),
                    'uid': room_data.get('uid'),
                    'live_status': room_data.get('live_status', 0)
                }
        
        return None
    except Exception as e:
        logger.error(f"Error checking live status for {room_id}: {e}")
        return None


def monitor_live_streams():
    """监听直播流"""
    logger.info("🚀 Starting Bilibili Live Stream Notifier...")
    
    while True:
        try:
            # 重新加载配置文件（支持动态添加房间）
            config = load_config()
            rooms = config.get('rooms', [])
            
            if not rooms:
                logger.warning("⚠️  No rooms configured!")
                time.sleep(config.get('check_interval', 300))
                continue
            
            logger.info(f"👀 Checking {len(rooms)} room(s)...")
            
            for room_config in rooms:
                room_id = room_config.get('room_id')
                anchor_name = room_config.get('anchor_name', 'Unknown')
                
                if not room_id:
                    logger.warning("Room ID not found in config!")
                    continue
                
                # 检查直播状态
                status_info = check_live_status(room_id)
                
                if not status_info:
                    logger.warning(f"⚠️  Failed to get status for room {room_id}")
                    continue
                
                live_status = status_info.get('live_status', 0)
                
                # 检查状态变化
                previous_status = live_status_cache.get(room_id, 0)
                
                if live_status != previous_status:
                    logger.info(f"🔄 Status change detected for room {room_id}: {previous_status} -> {live_status}")
                    
                    # 状态从 0 变为 1 或 2（开播）
                    if previous_status == 0 and live_status > 0:
                        logger.info(f"📢 Room {room_id} ({anchor_name}) is now live!")
                        
                        # 获取房间详情
                        room_info = get_room_info(room_id)
                        
                        # 发送通知
                        send_dingtalk_notification(config, room_id, anchor_name, live_status, room_info)
                    
                    # 更新缓存
                    live_status_cache[room_id] = live_status
                
                # 打印当前状态
                status_text = {0: '未开播', 1: '直播中', 2: '轮播中'}.get(live_status, '未知')
                logger.info(f"✓ Room {room_id} ({anchor_name}): {status_text}")
            
            # 等待下次检查
            check_interval = config.get('check_interval', 300)
            logger.info(f"⏳ Next check in {check_interval} seconds...\n")
            time.sleep(check_interval)
            
        except KeyboardInterrupt:
            logger.info("\n👋 Shutting down...")
            break
        except Exception as e:
            logger.error(f"❌ Error in monitoring loop: {e}")
            time.sleep(60)  # 出错后等待 60 秒再重试


if __name__ == '__main__':
    monitor_live_streams()
