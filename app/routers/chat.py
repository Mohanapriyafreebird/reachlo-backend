from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List
from app.database import get_db
from app.models import User, Campaign, Lead, ChatThread, ChatMessage, Business
from app.schemas import ChatThreadCreate, ChatMessageCreate, ChatMessageResponse, ChatThreadResponse, UnreadCountResponse
from app.dependencies import get_current_user, get_ws_current_user
from app.utils.push_notifications import send_new_lead_notification, send_new_message_to_buyer, send_new_message_to_seller
from app.utils.websocket_manager import manager
from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from datetime import datetime

router = APIRouter(prefix="/chat", tags=["Chat"])

def get_thread_display_info(thread: ChatThread, current_user: User):
    """Denormalise display fields based on who is asking."""
    res = ChatThreadResponse.from_orm(thread)
    res.campaign_title = thread.campaign.title if thread.campaign else "Unknown Campaign"
    res.campaign_image_url = thread.campaign.image_url if thread.campaign else None
    
    if thread.messages:
        last_msg = thread.messages[-1]
        res.last_message_body = last_msg.body
    
    res.total_messages = len(thread.messages)
    
    # If buyer is asking, show seller details. If seller is asking, show buyer details.
    if current_user.role == "BUYER":
        seller_business = thread.campaign.business if thread.campaign else None
        res.seller_name = seller_business.name if seller_business else "Seller"
        res.seller_phone = thread.seller.phone if thread.seller else None
        res.buyer_name = current_user.name
        res.buyer_phone = current_user.phone
    else:
        res.buyer_name = thread.buyer.name if thread.buyer else "Buyer"
        res.buyer_phone = thread.buyer.phone if thread.buyer else None
        res.seller_name = thread.campaign.business.name if thread.campaign and thread.campaign.business else "You"
        res.seller_phone = thread.seller.phone if thread.seller else None
        
    return res


@router.post("/threads", response_model=ChatThreadResponse, status_code=status.HTTP_201_CREATED)
def create_thread(
    payload: ChatThreadCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Called when a buyer claims a deal. 
    Requires the lead_id that was just created.
    """
    if current_user.role != "BUYER":
        raise HTTPException(status_code=403, detail="Only buyers can initiate chats by claiming a deal.")
        
    lead = db.query(Lead).filter(Lead.id == payload.lead_id).first()
    if not lead or lead.buyer_id != current_user.id:
        raise HTTPException(status_code=404, detail="Lead not found or unauthorized.")
        
    # Check if thread already exists
    existing_thread = db.query(ChatThread).filter(ChatThread.lead_id == lead.id).first()
    if existing_thread:
        return get_thread_display_info(existing_thread, current_user)
        
    campaign = lead.campaign
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")
        
    seller = campaign.business.user
    
    # 1. Create Thread
    thread = ChatThread(
        lead_id=lead.id,
        campaign_id=campaign.id,
        buyer_id=current_user.id,
        seller_id=seller.id,
        seller_unread_count=1,
        buyer_unread_count=0
    )
    db.add(thread)
    db.commit()
    db.refresh(thread)
    
    # 2. Add System Welcome Message
    welcome_msg = ChatMessage(
        thread_id=thread.id,
        sender_id=seller.id, # Attributed to seller but marked as system
        sender_role="SYSTEM",
        body=f"🎉 Thank you for your interest in \"{campaign.title}\"!\n\nWe've notified the seller about your enquiry. They'll reach out to you soon. In the meantime, feel free to ask any questions here — we're happy to help!\n\n— Team Reachlo",
        is_system=True
    )
    db.add(welcome_msg)
    db.commit()
    db.refresh(thread)
    
    # 3. Notify Seller via Push
    send_new_lead_notification(
        seller_token=seller.expo_push_token,
        campaign_title=campaign.title,
        buyer_name=current_user.name
    )
    
    return get_thread_display_info(thread, current_user)


@router.get("/threads", response_model=List[ChatThreadResponse])
def list_threads(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """List all threads for the authenticated user."""
    if current_user.role == "BUYER":
        threads = db.query(ChatThread).filter(ChatThread.buyer_id == current_user.id).order_by(ChatThread.last_message_at.desc()).all()
    else:
        threads = db.query(ChatThread).filter(ChatThread.seller_id == current_user.id).order_by(ChatThread.last_message_at.desc()).all()
        
    return [get_thread_display_info(t, current_user) for t in threads]


@router.get("/threads/{thread_id}/messages", response_model=List[ChatMessageResponse])
def list_messages(thread_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Get message history for a thread."""
    thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
    if not thread or (thread.buyer_id != current_user.id and thread.seller_id != current_user.id):
        raise HTTPException(status_code=404, detail="Thread not found.")
        
    messages = db.query(ChatMessage).filter(ChatMessage.thread_id == thread_id).order_by(ChatMessage.created_at.asc()).all()
    
    res = []
    for m in messages:
        r = ChatMessageResponse.from_orm(m)
        r.sender_name = m.sender.name if m.sender else None
        res.append(r)
        
    return res


@router.post("/threads/{thread_id}/messages", response_model=ChatMessageResponse)
async def send_message(
    thread_id: str,
    payload: ChatMessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Send a new message in a thread."""
    thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
    if not thread or (thread.buyer_id != current_user.id and thread.seller_id != current_user.id):
        raise HTTPException(status_code=404, detail="Thread not found.")
        
    msg = ChatMessage(
        thread_id=thread.id,
        sender_id=current_user.id,
        sender_role=current_user.role,
        body=payload.body,
        is_system=False
    )
    db.add(msg)
    
    # Update thread metadata and unread counts
    now = datetime.utcnow()
    thread.last_message_at = now
    if current_user.role == "BUYER":
        thread.seller_unread_count += 1
        thread.last_buyer_message_at = now
    else:
        thread.buyer_unread_count += 1
        thread.last_seller_reply_at = now
        
    db.commit()
    db.refresh(msg)
    
    # Push Notifications
    campaign_title = thread.campaign.title if thread.campaign else "a campaign"
    if current_user.role == "BUYER":
        seller = thread.seller
        send_new_message_to_seller(seller.expo_push_token, current_user.name, campaign_title, msg.body)
    else:
        buyer = thread.buyer
        seller_business = thread.campaign.business if thread.campaign else None
        s_name = seller_business.name if seller_business else current_user.name
        send_new_message_to_buyer(buyer.expo_push_token, s_name, campaign_title, msg.body)

    r = ChatMessageResponse.from_orm(msg)
    r.sender_name = current_user.name
    
    # Broadcast to connected sockets
    msg_dict = r.dict()
    msg_dict["created_at"] = msg_dict["created_at"].isoformat()
    # Ensure both sides receive real-time message if connected
    await manager.send_personal_message(msg_dict, thread.buyer_id)
    await manager.send_personal_message(msg_dict, thread.seller_id)
    
    # Auto-Responder Logic
    if current_user.role == "BUYER":
        auto_reply_body = None
        text = payload.body.lower()
        
        # Determine human-like response based on category or intent
        if any(word in text for word in ["price", "pricing", "cost", "fee", "how much"]):
            if thread.campaign and thread.campaign.price:
                auto_reply_body = f"Hi! Thanks for reaching out. The pricing for this package is ₹{thread.campaign.price}. Would you like me to explain what's included?"
            else:
                auto_reply_body = "Hi! Our pricing varies based on your exact requirements. Could you share a few more details so I can give you an accurate estimate?"
                
        elif any(word in text for word in ["location", "direction", "where", "address", "visit"]):
            if thread.campaign and thread.campaign.location_address:
                auto_reply_body = f"Hello! We are located at {thread.campaign.location_address}. Would you like to schedule a visit?"
            else:
                auto_reply_body = "Hello! Let me get you those details. Where are you currently traveling from?"
                
        elif any(word in text for word in ["offer", "discount", "special", "promo"]):
            if thread.campaign and thread.campaign.offer:
                auto_reply_body = f"Hi there! Yes, currently we have a special offer: {thread.campaign.offer}. It's a great time to join! Would you like to claim it?"
            else:
                auto_reply_body = "Hi there! I can definitely check what current offers we have available for you. Are you looking to sign up this week?"
                
        if auto_reply_body:
            # Send as SELLER (human-like) instead of SYSTEM
            sys_msg = ChatMessage(
                thread_id=thread.id,
                sender_id=thread.seller_id,
                sender_role="SELLER",
                body=auto_reply_body,
                is_system=False
            )
            db.add(sys_msg)
            now = datetime.utcnow()
            thread.last_message_at = now
            thread.last_seller_reply_at = now
            thread.buyer_unread_count += 1
            db.commit()
            db.refresh(sys_msg)
            
            s_r = ChatMessageResponse.from_orm(sys_msg)
            s_r.sender_name = thread.campaign.business.name if thread.campaign and thread.campaign.business else "Business Representative"
            sys_msg_dict = s_r.dict()
            sys_msg_dict["created_at"] = sys_msg_dict["created_at"].isoformat()
            
            await manager.send_personal_message(sys_msg_dict, thread.buyer_id)
            await manager.send_personal_message(sys_msg_dict, thread.seller_id)
    
    return r


@router.post("/threads/{thread_id}/read")
async def mark_read(thread_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Mark a thread as read (resets unread counter for caller)."""
    thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
    if not thread or (thread.buyer_id != current_user.id and thread.seller_id != current_user.id):
        raise HTTPException(status_code=404, detail="Thread not found.")
        
    if current_user.role == "BUYER":
        thread.buyer_unread_count = 0
        target_id = thread.seller_id
    else:
        thread.seller_unread_count = 0
        target_id = thread.buyer_id
        
    db.commit()
    
    await manager.send_personal_message({
        "type": "MARK_READ",
        "thread_id": thread_id,
        "user_id": current_user.id
    }, target_id)
    
    return {"success": True}


@router.get("/unread-count", response_model=UnreadCountResponse)
def get_unread_count(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Get total unread message count across all threads."""
    if current_user.role == "BUYER":
        threads = db.query(ChatThread).filter(ChatThread.buyer_id == current_user.id, ChatThread.buyer_unread_count > 0).all()
        total = sum(t.buyer_unread_count for t in threads)
    else:
        threads = db.query(ChatThread).filter(ChatThread.seller_id == current_user.id, ChatThread.seller_unread_count > 0).all()
        total = sum(t.seller_unread_count for t in threads)
        
    return {"unread_count": total}

@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str,
    db: Session = Depends(get_db)
):
    try:
        user = await get_ws_current_user(token, db)
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect(websocket, user.id)
    try:
        while True:
            # We can accept incoming messages via WS, but for now we'll just listen to keep connection open
            # and allow the client to send read receipts or typing indicators if needed.
            data = await websocket.receive_json()
            if data.get("type") == "TYPING":
                thread_id = data.get("thread_id")
                # broadcast typing event to the other party
                thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
                if thread:
                    target_id = thread.seller_id if user.id == thread.buyer_id else thread.buyer_id
                    await manager.send_personal_message({
                        "type": "TYPING",
                        "thread_id": thread_id,
                        "user_id": user.id
                    }, target_id)
            elif data.get("type") == "MARK_READ":
                thread_id = data.get("thread_id")
                thread = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
                if thread:
                    if user.role == "BUYER":
                        thread.buyer_unread_count = 0
                    else:
                        thread.seller_unread_count = 0
                    db.commit()
    except WebSocketDisconnect:
        manager.disconnect(websocket, user.id)
