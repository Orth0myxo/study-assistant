from __future__ import annotations

from app.services.agent_store import AgentConversation, AgentStore


def test_agent_store_scopes_conversations_results_and_files(tmp_path):
    store = AgentStore()
    store.configure(60, tmp_path)
    owner_result = store.save_result(
        "owner-a", "doc-a", "passages", {"pages": [2], "section_indexes": [1]}
    )
    conversation = AgentConversation(
        store.new_id(),
        "owner-a",
        owner_result.result_id,
        "doc-a",
        ["cytology"],
        last_source_id="doc-a",
        last_source_kind="pdf",
    )
    store.save_conversation(conversation)
    generated = store.save_file("owner-a", b"export", "notes.docx", "docx")
    job = store.save_export_job("owner-a", owner_result.result_id, "docx")

    assert store.get_conversation(conversation.conversation_id, "owner-a")
    assert store.get_conversation(conversation.conversation_id, "owner-b") is None
    reloaded = store.get_conversation(conversation.conversation_id, "owner-a")
    assert reloaded.last_source_id == "doc-a"
    assert reloaded.last_source_kind == "pdf"
    assert store.get_result(owner_result.result_id, "owner-a").payload == {
        "pages": [2],
        "section_indexes": [1],
    }
    assert store.get_result(owner_result.result_id, "owner-b") is None
    assert store.get_file(generated.file_id, "owner-a").path.read_bytes() == b"export"
    assert store.get_file(generated.file_id, "owner-b") is None
    assert store.get_export_job(job.job_id, "owner-a").status == "queued"
    assert store.get_export_job(job.job_id, "owner-b") is None
    job.status = "completed"
    job.file_id = generated.file_id
    store.update_export_job(job)
    assert store.get_export_job(job.job_id, "owner-a").file_id == generated.file_id

    store.clear()
