/** Solo Dev LLM Bench - Results page delete modal logic (extracted). */

// Delete modal references
var deleteModal = document.getElementById("delete-modal");
var deleteModalBody = document.getElementById("delete-modal-body");
var cancelDeleteBtn = document.getElementById("cancel-delete");
var confirmDeleteBtn = document.getElementById("confirm-delete");

// Current pending delete state
var pendingDeleteRunId = null;
var pendingDeleteModel = "";
var pendingDeleteTimestamp = "";

// ---------------------------------------------------------------------------
// Delete functionality
// ---------------------------------------------------------------------------

function openDeleteModal(runId, model, timestamp) {
    pendingDeleteRunId = runId;
    pendingDeleteModel = model;
    pendingDeleteTimestamp = timestamp;

    var tsDisplay = formatTimestamp(timestamp);
    deleteModalBody.innerHTML =
        '<p><strong>Model:</strong> ' + escapeHtml(model) + '</p>' +
        '<p><strong>Run ID:</strong> <code>' + escapeHtml(runId) + '</code></p>' +
        '<p><strong>Date:</strong> ' + tsDisplay + '</p>' +
        '<p style="margin-top:0.75rem;color:#dc2626;">This permanently removes this result.</p>';

    deleteModal.classList.remove("hidden");
}

function closeDeleteModal() {
    pendingDeleteRunId = null;
    pendingDeleteModel = "";
    pendingDeleteTimestamp = "";
    deleteModal.classList.add("hidden");
}

async function executeDelete() {
    if (!pendingDeleteRunId) {
        closeDeleteModal();
        return;
    }

    // Save runId BEFORE closing modal — closeDeleteModal() clears pending state
    var runId = pendingDeleteRunId;
    var model = pendingDeleteModel;
    var timestamp = pendingDeleteTimestamp;

    closeDeleteModal();

    try {
        var resp = await fetch("/api/results/" + encodeURIComponent(runId), {
            method: "DELETE"
        });

        if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            throw new Error(err.detail || "HTTP " + resp.status);
        }

        // Reload results after deletion
        await loadResults();
    } catch (e) {
        showStatus("Failed to delete run: " + e.message, "error");
    }
}

// ---------------------------------------------------------------------------
// Delete modal event listeners
// ---------------------------------------------------------------------------

cancelDeleteBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    closeDeleteModal();
});
confirmDeleteBtn.addEventListener("click", executeDelete);

// Close modal on overlay click
if (deleteModal) {
    deleteModal.addEventListener("click", function (e) {
        if (e.target === deleteModal) {
            closeDeleteModal();
        }
    });
}

// Close modal on Escape key
document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !deleteModal.classList.contains("hidden")) {
        closeDeleteModal();
    }
});