# Generic Surgical Synchronization Protocol (GSSP) Design

## 1. Overview
This document outlines a generic synchronization protocol designed for the application's data types (Todos, Lists, Hashtags, etc.). 
The protocol is designed to:
1.  **Minimize Bandwidth**: Send only what has changed (deltas/patches).
2.  **Support Offline & Web Clients**: Unified behavior for persistent offline clients (sync) and transient web clients (updates).
3.  **Single Endpoint**: Perform push and pull in a single HTTP `POST` call (in most cases).
4.  **Granular/Surgical**: Use a Merkle Tree structure to identify exactly which entities need transferring without sending entire tables.

## 2. Data Model
The protocol is generic but is specifically tailored for the following entities identified in the codebase:
-   **Lists**
-   **Todos** (Atomic items)
-   **Hashtags**
-   **SmartFolders**
-   **CalendarEvents** & **Completions**
-   **Notes**
-   **Links**
-   **Metadata** (User Settings, etc.)

Each entity is treated as a uniform "Record" with:
-   `type`: (e.g., "todo")
-   `id`: (Unique Identifier, UUID or Int)
-   `version`: (Timestamp or Lamport Clock)
-   `hash`: (SHA256 of the content)
-   `content`: (The actual JSON data)

## 3. State Representation: The Merkle Tree

To achieve surgical synchronization, the state of the database is represented as a Merkle Tree.

### Tree Structure
Instead of hashing entire collections (e.g., "all todos"), we use a finer granularity:

1.  **Root Node**: Hash of all Type Nodes.
2.  **Type Node** (e.g., "Todos"): Hash of all Shard Nodes (or direct Entity hashes if small).
3.  **Entity Leaf**: Hash of the individual entity's content.

```text
       [ROOT HASH]
      /           \
  [Todos]       [Lists]
   /   \         /   \
[T_1] [T_2]   [L_1] [L_2] ...
```

This allows the client and server to pinpoint exactly *which* item differs.

## 4. The Protocol

**Endpoint**: `POST /api/v1/sync`

The sync process is a "State Transition" negotiation. The client proposes a transition (its local changes) and typically requests the new state.

### Phase 1: The "Surgical" Exchange (Standard Case)
This handles 99% of cases: normal web updates or syncing after a short offline period.

**Response Goal**: Complete sync in **1 RTT**.

#### Request Payload
The client sends a JSON payload containing:
1.  `basis_hash`: The Merkle Root hash the client *started* with (before its local offline changes).
2.  `mutations`: A list of changes the client wants to apply (Create, Update, Delete).
    -   *Crucial*: If the client is a web client making one change, it sends that one change.
    -   If the client is an offline client coming back, it sends all offline edits.
3.  `request_diff`: `true` (asks server to send back any updates the client is missing).

```json
{
  "basis_hash": "a1b2c3d4...",
  "mutations": [
    { "op": "update", "type": "todo", "id": 101, "data": { "completed": true } }
  ],
  "request_diff": true
}
```

#### Server Logic
1.  **Apply Mutations**: The server attempts to apply the client's `mutations` to its current DB.
    -   *Conflict Resolution*: Last-Write-Wins (LWW) or CRDT logic is applied here.
2.  **Recompute Root**: The server calculates the new global Merkle Root.
3.  **Compute Delta**: The server compares the Client's expected state (Basis + Mutations) vs. the Server's Current State.
    -   Since the server knows exactly what changed globally since `basis_hash`, it identifies entities that the Client doesn't have.
4.  **Send Response**: The server sends the new root and the missing data.

#### Response Payload
```json
{
  "new_root_hash": "x9y8z7...",
  "status": "synced",
  "patches": [
    { "op": "update", "type": "todo", "id": 500, "data": { "title": "New server item" } }
  ]
}
```

**Result**: Client applies `patches`. Client verifies its local Merkle Root matches `new_root_hash`. Sync complete. 1 Call.

---

### Phase 2: The "Tree Walk" (Recovery/Bulk Case)
If the client is very far behind, or `basis_hash` is unknown/too old, or the delta is massive, the server might fall back to a tree comparison protocol (max 2 calls).

#### Scenario: `basis_hash` Unknown
If the client sends a `basis_hash` the server doesn't recognize (or if client sends `null` because it's a fresh install):

**Call 1 (Probe)**:
Client sends Request with `basis_hash: null`.

**Response 1 (Tree Offer)**:
Server sends the high-level Merkle nodes instead of data.
```json
{
  "status": "partial",
  "tree_diff": {
    "todos": "hash_of_all_todos",
    "lists": "hash_of_all_lists"
  }
}
```

**Call 2 (Drill Down / Bulk Fetch)**:
Client compares `tree_diff` with local state. It sees "Todos" mismatch. It requests specific data.
```json
{
  "fetch": ["todos"] 
  // OR if "todos" is too big, it requests sub-tree hashes to drill down further
}
```

**Response 2**: Server sends the requested data.

## 5. Implementation Strategy for "Intelligence"

To ensure the "Protocol manages the Logic", specific fields allow "Surgical" placement:

-   **`modulator`**: Not needed. The data type matches the DB schema.
-   **Payload Construction**:
    -   **Client**: "I have changed Todo #123. I will only add Todo #123 to the `mutations` list. everything else is implied unchanged."
    -   **Server**: "I see you changed #123. But #456 also changed since your basis. I will add #456 to the `patches` list."

### Conflict Handling
-   If Client changes Todo #1 and Server changed Todo #1:
    -   Server wins (usually). The `patches` response will contain the Server's version of Todo #1, overwriting the client's pending change.
    -   Client UI updates to reflect server state.

## 6. Offline Capabilities
-   Clients store the `root_hash` locally.
-   Clients queue `mutations` locally while offline.
-   On reconnect, they simply send the standard Phase 1 request.

## Summary
| Feature | Implementation |
| :--- | :--- |
| **Endpoint** | Single JSON POST. |
| **Round Trips** | 1 for normal ops (99%), 2 for cold sync. |
| **Efficiency** | Only deltas transferred. |
| **Integrity** | Merkle Root validation ensures convergence. |
