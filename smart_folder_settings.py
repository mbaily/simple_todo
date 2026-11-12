"""Normalize inheritance at input boundaries; SmartFolder owns the only stored values."""


def flag(value):
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def inheritance_settings(data):
    """Explicit folder settings win, including False. Old nested tag flags are
    combined with ANY only when a folder setting is absent. Omitted values stay
    omitted so partial imports/sync do not reset existing settings.
    """
    result = {}
    for field, collections in (
        ("include_subtodos", ("include_hashtags", "exclude_hashtags")),
        ("include_list_items", ("include_hashtags",)),
    ):
        if field in data:
            result[field] = flag(data[field])
            continue
        legacy = []
        for name in collections:
            records = data.get(name, data.get(name + "_assoc"))
            if isinstance(records, list):
                legacy.extend(record[field] for record in records if isinstance(record, dict) and field in record)
        if legacy:
            result[field] = any(map(flag, legacy))
    return result


def form_inheritance_settings(form, include_ids, exclude_ids):
    """Accept the current common switches and older per-tag HTML forms."""
    data = dict(form)
    data["include_hashtags"] = [
        {field: form[f"{field}_{tag_id}"] for field in ("include_subtodos", "include_list_items")
         if f"{field}_{tag_id}" in form}
        for tag_id in include_ids
    ]
    data["exclude_hashtags"] = [
        {"include_subtodos": form[f"exclude_subtodos_{tag_id}"]}
        for tag_id in exclude_ids if f"exclude_subtodos_{tag_id}" in form
    ]
    # A submitted HTML form is complete: an unchecked common switch is False.
    return {"include_subtodos": False, "include_list_items": False, **inheritance_settings(data)}
