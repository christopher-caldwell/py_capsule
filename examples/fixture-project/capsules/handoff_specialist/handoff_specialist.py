SPECIALIST_NUMBER = "+15551234567"  # TODO: replace with real specialist line

Conversation.set_external_id(f"app-voice-v2-outbound:{contact_phone_number}")
Conversation.merge_metadata({
    "contact_name": contact_name,
    "summary_text": summary_text,
    "goals": goals,
    "involvement_level": involvement_level,
    "status_note": status_note,
    "is_away": is_away,
    "handoff_target": "specialist",
})
Conversation.set_metadata("handoff_done", True)
Conversation.dial_number(SPECIALIST_NUMBER)

return {"success": True, "message": "Transferring to specialist line"}
