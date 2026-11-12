# P2P / Local-First Refactoring Plan

## Objective
Refactor the existing server-based `simple_todo` application into a **Local-First (P2P)** architecture. 
The goal is to allow every client (web, desktop, mobile) to possess a full copy of the business logic and data ("Shared Core"), enabling them to operate offline and synchronize (merge) with other peers.

## Core Philosophy: The Git Analogy

| Feature | Server-Based (Current) | P2P / Local-First (Target) |
| :--- | :--- | :--- |
| **Logic** | Live on Server | **Shared Core** embedded in every client |
| **State** | Single Source of Truth (DB) | **Distributed** (Every peer has a DB) |
| **Sync** | API calls (CRUD) | **Merging States** (Diff/Patch or Merkle Tree) |
| **Storage**| PostgreSQL/SQLite (Server) | **Abstracted**: SQLite (Desktop) / Native IndexedDB (Web) |

---

## 1. The Storage Abstraction Layer

Unlike Git, which assumes a filesystem, our P2P app must run in environments with vastly different storage capabilities (e.g., Browser vs OS). To support this, we must decouple the *Business Logic* from the *Data Persistence*.

### The Repository Pattern
The `todo_core` will define a set of abstract interfaces (Protocols) that the logic uses.

```python
class StorageInterface(Protocol):
    def get_todo(self, id: str) -> Optional[Todo]: ...
    def save_todo(self, todo: Todo) -> None: ...
    def get_merkle_root(self) -> str: ...
    def apply_patch(self, patch: Dict) -> None: ...
```

### Concrete Implementations

#### A. Python / Desktop / Server (SQLAlchemy + SQLite)
*   **Implementation**: `SqliteStorage(StorageInterface)`
*   **Technology**: SQLAlchemy (Sync/Async).
*   **Format**: `local_todo.db` file.
*   **Pros**: Reuses existing code, fast, relational integrity.
*   **Usage**: The server node, desktop CLI, and desktop GUI.

#### B. The Web Client: Native Web Storage (IndexedDB)
*   **Implementation**: `IndexedDBRepository` (TypeScript/JavaScript).
*   **Technology**: `Dexie.js` (wrapper around IndexedDB).
*   **Format**: Browser's native IndexedDB object stores.
*   **Why**: 
    *   **Native Performance**: Fastest possible storage for the browser.
    *   **Zero-Dependency**: No need to load heavy WASM binaries (like Pyodide or SQLite-wasm).
    *   **Standard**: First-class citizen of the web ecosystem.

---

## 2. Language Strategy: Dual Implementations vs. Node.js Refactor

Adopting Native Web Storage implies we cannot run the Python `todo_core` directly in the browser (without WASM). This presents a strategic choice:

### Option A: Dual Port (Maintained Concurrency)
We maintain two identical implementations of the Core Logic.
1.  `todo_core_py`: Python package for TUI, Desktop, and Server Relay.
2.  `todo_core_js`: NPM package for the Web Client.

*   **Pros**:
    *   Keeps the Server/Desktop ecosystem in Python (rich ecosystem, TUI libs).
    *   No need to rewrite the entire backend/desktop app in JS.
*   **Cons**:
    *   **Logic Drift**: High risk that `merkle_sync.py` and `merkle_sync.js` diverge, causing sync corruption.
    *   **Double Work**: Every feature must be coded twice.

### Option B: Node.js / TypeScript Switch (Recommended for Stability)
We rewrite the Core Logic *once* in TypeScript (`todo_core_ts`) and use it everywhere.
1.  **Web**: Imports `todo_core_ts` directly.
2.  **Server**: Runs Node.js, uses `todo_core_ts` + SQLite (better-sqlite3).
3.  **Desktop/TUI**:
    *   *Path 1*: Rewrite TUI in JS (Ink/Blessed).
    *   *Path 2 (Hybrid)*: Python TUI spawns a small Node.js sidecar for core logic.

### Recommendation: Dual Port with Specification Testing
Since the User has a Python TUI and Server they like, **Option A (Dual Port)** is the most improved path *if* we mitigate the risks.

**Mitigation Strategy: The Compliance Suite**
We create a language-agnostic test suite (JSON inputs/outputs).
*   `test_cases.json`: Contains [State A, Action, Expected State B].
*   Both `todo_core_py` and `todo_core_js` must pass this exact suite in CI.
*   This ensures `merkle_sync.js` behaves *exactly* like `merkle_sync.py`.

---

## 3. Architecture Components

### The Shared Core (`todo_core`)
A standalone package (conceptually shared, physically implemented twice) containing:

1.  **Domain Models**: Pydantic models (Py) / Zod schemas (TS).
2.  **Business Rules**: Pure functions.
    *   `move_folder_logic(tree_state, move_cmd) -> new_tree_state`
3.  **Sync Engine**:
    *   `compute_merkle_diff(local_store, remote_hash)`
    *   `resolve_conflict(local_item, remote_item)`

### The Interfaces (Adapters)

*   **FastAPI Node**:
    *   Loads `SqliteStorage` (Python).
    *   Exposes HTTP endpoints for `sync_push` / `sync_pull`.
*   **Browser App**:
    *   Loads `todo_core_js`.
    *   Uses `IndexedDBRepository`.

---

## 4. Refactoring Roadmap

### Phase 1: Decoupling Storage (Python Side)
*Goal: Remove direct SQLAlchemy sessions from Business Logic.*

1.  **Create `todo_core/interfaces.py`**: Define `StorageReader` and `StorageWriter`.
2.  **Refactor Services**: Change functions to accept `StorageInterface` instead of `db: Session`.

### Phase 2: The Compliance Suite
*Goal: Create the "Truth" for dual implementations.*

1.  **Define Canonical JSON**: The "Wire Format" for a Todo/List must be strictly defined (Pydantic/Zod).
2.  **Generate Test Vectors**: Create a set of complex sync scenarios (merges, conflicts, moves) and save expected hashes/outputs to JSON.

### Phase 3: Web Client Implementation (JavaScript Port)
1.  **Implement `IndexedDBRepository`**: Map the Canonical JSON to IndexedDB Object Stores using Dexie.js.
2.  **Port Sync Logic**: Write `merkle_sync.js` in TypeScript.
3.  **Verify**: Run the Compliance Suite against the JS implementation.

---

## 5. Conflict Resolution & Consistency
Since we have different storage engines (SQL tables vs Object Stores), **Serialization is Key**.

*   **Critical Rule**: Hashing happens on the **Canonical JSON**, NOT the database row.
*   `Object` -> `Canonical JSON` -> `SHA256` -> `Store (SQLite/IndexedDB)`.
*   This guarantees that if the Web Client (IndexedDB) and Server (SQLite) have the same logical data, they verify as "In Sync" (Same Merkle Root).
