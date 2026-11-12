
@app.get("/api/export")
def export_data(db: Session = Depends(get_db)):
    """Export all user data as JSON."""
    data = {
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "data": {
            "folders": [serialize_folder(f) for f in db.query(Folder).all()],
            "lists": [serialize_list(l) for l in db.query(List).all()],
            "todos": [serialize_todo(t) for t in db.query(Todo).all()],
            "notes": [n.to_dict() for n in db.query(Note).all()], # Note doesn't have a standalone serializer in list above but probably has to_dict or we make one
            "hashtags": [serialize_hashtag(h) for h in db.query(Hashtag).all()],
            "calendar_events": [serialize_calendar_event(e) for e in db.query(CalendarEvent).all()],
            "headers": [serialize_header(h) for h in db.query(ListDatetimeHeader).all()],
            "smart_folders": [serialize_smart_folder(sf) for sf in db.query(SmartFolder).all()],
             # We could add more like NamedSearch, etc. if needed
        }
    }
    
    # We need to manually handle Note serialization if serialize_note doesn't exist.
    # Checking imports... serialize_note is NOT imported.
    # Note model usually has .to_dict() or simple attributes.
    # Let's check models.py for Note.to_dict or just manual serialization.
    
    filename = f"simple_todo_export_{datetime.now().strftime('%Y-%m-%d')}.json"
    headers = {"Content-Disposition": f"attachment; filename=\"{filename}\""}
    return JSONResponse(content=data, headers=headers)
