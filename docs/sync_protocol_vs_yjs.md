# Protocol Comparison: Custom (GSSP) vs. Yjs

This document outlines the trade-offs between implementing the custom **Generic Surgical Synchronization Protocol (GSSP)** designed in `sync_protocol.md` and adopting **Yjs**, a popular Commutative Replicated Data Type (CRDT) library.

## at a Glance

| Feature | Custom GSSP (Merkle-Based) | Yjs (CRDT-Based) |
| :--- | :--- | :--- |
| **Conflict Resolution** | **Server-Authoritative / LWW**. Simple "Last Write Wins" or custom business logic. Server decides the final state. | **Mathematical Convergence**. Automatic merging of concurrent edits (e.g., two people typing in the same word). |
| **Data Format** | **Standard JSON**. Human-readable, easy to debug, standard HTTP/REST tooling. | **Binary (Uint8Array)**. Highly optimized, opaque. Requires Yjs library to decode/read. |
| **Network Protocol** | **Stateless HTTP (REST)**. Works perfectly with standard web servers (FastAPI, Nginx). "Surgical" 1-request updates. | **Stateful (usually)**. Best with WebSockets (ws). Can work over HTTP, but requires managing "State Vectors" and binary blobs. |
| **Complexity** | **High Implementation / Low Concept**. You build the sync logic yourself, but the logic is easy to understand. | **Low Implementation / High Concept**. You just "plug it in", but understanding *how* it works (vectors, tombstones) is hard. |
| **Bandwidth** | **Surgical**. Sends *exact* JSON for changed items. Very predictable. | **Compressed**. Very efficient, but history ("tombstones") can grow over time if not garbage collected. |
| **Offline Support** | **Snapshots**. Client stores JSON. Re-syncs by calculating hash differences. | **Journaling**. Client stores binary update logs. Replays logs into the document on load. |

---

## Detailed Breakdown

### 1. The "Surgical" Requirement
**Our Goal**: *Ensure updates occur in just one JSON endpoint call, sending the least amount of data.*

*   **Custom GSSP**: Ideally suited.
    *   If a client updates 1 Todo, it sends a JSON payload with *just* that Todo's new state.
    *   The Server validates, saves, and returns the new Merkle Root. Use of JSON makes it trivial to map to your SQL database.
*   **Yjs**: Slightly different paradigm.
    *   Yjs syncs "Update Blobs". If you change a Todo, Yjs generates a small binary update.
    *   You send this binary blob to the server. The server must load the Yjs "Doc", apply the blob, and then save the Doc back to the DB (or use a Yjs-provider database adapter).
    *   **Friction**: You lose the direct mapping between "A Todo Row in SQL" and "The data on the wire". The data on the wire is a generic CRDT update sequence.

### 2. Conflict Handling & Collaboration
**Our Goal**: *Offline clients sync up; users edit lists.*

*   **Custom GSSP**: Last-Write-Wins (LWW).
    *   If User A changes Title to "Foo" offline, and User B changes it to "Bar" online...
    *   When User A connects, the server sees the timestamps. If User A is "newer", "Foo" overwrites "Bar".
    *   **Pros**: Simple, predictable for "Item settings" (like `completed=true`).
    *   **Cons**: Bad for *collaborative text editing*. If two users edit the *description* note simultaneously, one will overwrite the other completely.
*   **Yjs**: CRDT Magic.
    *   If User A and B edit the note simultaneously, Yjs merges the characters (e.g., "Fbaoar").
    *   **Pros**: Perfect for collaborative text (Google Docs style) or lists where order matters intensely (avoiding index collisions).
    *   **Cons**: Overkill for simple boolean toggles (`completed`), and adds complexity to the backend storage.

### 3. Backend Integration
**Our Goal**: *Simple Todo App with SQLite/SQLAlchemy.*

*   **Custom GSSP**: Native integration.
    *   Your existing SQLAlchemy models (`models.py`) Remain the source of truth.
    *   The sync logic is just a translation layer: `Model -> JSON -> Merkle Hash`.
    *   You can query the DB directly (`SELECT * FROM todos`) and it makes sense.
*   **Yjs**: Alien host.
    *   Yjs typically wants to own the data layout.
    *   To use Yjs with SQLite, you essentially store the Yjs binary blobs in the DB.
    *   To query "All uncompleted todos" via SQL, you often have to run a separate "Indexer" process that reads the Yjs doc and writes to a standard SQL table, adding architectural complexity.

### 4. Bandwidth & "One Endpoint"
*   **Custom GSSP**:
    *   Designed specifically for this. The `POST /sync` endpoint accepts simple JSON mutations.
    *   You have total control over the payload size.
*   **Yjs**:
    *   Yjs updates are small, but the "Initial Sync" involves exchanging "State Vectors" (summaries of what each side has).
    *   This usually requires a 2-step handshake (Exchange Vectors -> Exchange Data), though it can be bundled.
    *   The payload is opaque binary, which might be harder to debug if something goes wrong ("Why is this sync 4MB?").

## Recommendation

**Stick with the Custom GSSP (Merkle Approach) IF:**
1.  **Text Collaboration is NOT a priority**: You don't need two people typing in the same Note at the same exact second.
2.  **SQL Inspection is vital**: You want your SQLite database to look like normal tables, not binary blobs.
3.  **Simplicity of Debugging**: You want to read the JSON payloads in the network tab.
4.  **"Surgical" Control**: You want precise control over exactly when and what data is sent.

**Choose Yjs IF:**
1.  **Real-time Collaboration**: You need Google Docs-style simultaneous editing.
2.  **Complex List Operations**: You have heavy reordering needs where simple "sort_order" integers cause conflicts.
3.  **Library Reliability**: You prefer trusting a battle-tested library over maintaining your own sync protocol logic.
