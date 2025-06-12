import datetime
import json
import os
from typing import List

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from vcon import Vcon
from vcon.dialog import Dialog
from vcon.party import Party

from database import SessionLocal
from model import Conversation

# Extend Vcon to include thread_id and JSON serialization
DATABASE_URL = os.getenv("DATABASE_URL")
app = FastAPI()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class VconExtended(Vcon):
    @classmethod
    def build_new(cls, *, thread_id: str = None):
        inst: VconExtended = super().build_new()
        inst.thread_id = thread_id
        return inst

    def to_dict(self):
        base_dict = json.loads(super().to_json())
        if hasattr(self, "thread_id") and self.thread_id is not None:
            base_dict["thread_id"] = self.thread_id
        return base_dict


class DialogIn(BaseModel):
    originator_index: int = Field(...,
                                  description="Index of sender in parties list")
    body_snippet: str = Field(...,
                              description="Plain-text snippet of the email body")
    last_modified: datetime.datetime = Field(
        ..., description="Last modified timestamp of the email")


class EmailThreadRequest(BaseModel):
    thread_id: str = Field(..., description="Unique email thread identifier")
    parties: List[EmailStr] = Field(
        ..., description="List of participant email addresses, first is sender")
    dialogs: List[DialogIn] = Field(
        ..., description="List of dialog entries in chronological order")


def create_email_thread_vcon(req: EmailThreadRequest) -> VconExtended:
    vcon = VconExtended.build_new(thread_id=req.thread_id)

    for email in req.parties:
        vcon.add_party(Party(email=email))

    # Add dialogs
    party_indices = list(range(len(req.parties)))
    for dlg in req.dialogs:
        d = Dialog(
            type="text",
            start=dlg.last_modified.replace(
                tzinfo=datetime.timezone.utc).isoformat(),
            parties=party_indices,
            originator=dlg.originator_index,
            mimetype="text/plain",
            body=dlg.body_snippet
        )
        vcon.add_dialog(d)

    return vcon


@app.post("/vcon/email_thread")
def generate_email_vcon(request: EmailThreadRequest, db: Session = Depends(get_db)) -> dict:
    """
    Generate or update a Vcon JSON for an email thread and store it in the database.
    If the thread exists, merge new parties and dialogs, overriding dialog body and start if present.
    """
    vcon = create_email_thread_vcon(request)
    vcon_dict = vcon.to_dict()

    conversation = db.query(Conversation).filter(
        Conversation.threadId == request.thread_id).first()

    if conversation:
        existing_vcon = conversation.vcon

        # --- Merge Parties ---
        existing_parties = [p.get("email")
                            for p in existing_vcon.get("parties", [])]
        new_parties = []
        for email in request.parties:
            if email not in existing_parties:
                new_parties.append({"email": email})
        existing_vcon["parties"].extend(new_parties)

        # --- Merge or Override Dialogs ---
        existing_dialogs = existing_vcon.get("dialog", [])
    
        existing_bodies = {
            (d.get("body"), d.get("start")): idx
            for idx, d in enumerate(existing_dialogs)
        }
        for dlg in request.dialogs:
            dlg_start = dlg.last_modified.replace(
                tzinfo=datetime.timezone.utc).isoformat()
            dlg_tuple = (dlg.body_snippet, dlg_start)
            if dlg_tuple in existing_bodies:
               
                idx = existing_bodies[dlg_tuple]
                existing_dialogs[idx]["body"] = dlg.body_snippet
                existing_dialogs[idx]["start"] = dlg_start
               
            else:
                # Add new dialog
                new_dialog = {
                    "type": "text",
                    "start": dlg_start,
                    "parties": list(range(len(existing_vcon["parties"]))),
                    "originator": dlg.originator_index,
                    "mimetype": "text/plain",
                    "body": dlg.body_snippet,
                    "metadata": {},
                    "meta": {}
                }
                existing_vcon["dialog"].append(new_dialog)

        # Explicitly mark the vcon field as modified
        conversation.vcon = existing_vcon
        flag_modified(conversation, "vcon")
        conversation.updatedAt = datetime.datetime.now()
    else:
        conversation = Conversation(
            vcon=vcon_dict,
            threadId=request.thread_id
        )
        db.add(conversation)

    db.commit()
    db.refresh(conversation)
    return conversation.vcon


@app.get("/vcon/{thread_id}")
def get_vcon_by_thread_id(thread_id: str, db: Session = Depends(get_db)) -> dict:
    """Retrieve a Vcon from the Database"""
    conversation = db.query(Conversation).filter(
        Conversation.threadId == thread_id).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation.vcon
