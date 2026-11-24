"""One user's documents, jobs and answers must never be visible to, or affected by, another user."""
from app import auth, ingest
from tests.conftest import ADMIN_NAME, make_pdf, wait_for_job


def add(browser, text: str, name: str) -> dict:
    job = browser.post("/documents", files={"file": (name, make_pdf(text), "application/pdf")}).json()
    assert wait_for_job(browser, job["id"])["status"] == "done"
    return job


def names(browser) -> list[str]:
    return [d["source"] for d in browser.get("/documents").json()]


def test_each_user_has_a_library_of_their_own(client, new_user):
    new_user("bob")
    assert auth.get_user_by_name(ADMIN_NAME).collection != auth.get_user_by_name("bob").collection


def test_users_only_see_their_own_documents(client, new_user, fake_gemini):
    bob = new_user("bob")
    add(client, "getopt admin only", "admins.pdf")
    add(bob, "strtok bobs notes", "bobs.pdf")
    assert names(client) == ["admins.pdf"]
    assert names(bob) == ["bobs.pdf"]


def test_a_new_user_starts_with_an_empty_library_even_if_others_have_documents(client, new_user, fake_gemini):
    add(client, "getopt admin only", "admins.pdf")
    assert names(new_user("bob")) == []


def test_answers_never_use_someone_elses_documents(client, new_user, fake_gemini):
    bob = new_user("bob")
    add(client, "getopt confidential admin text", "admins.pdf")
    assert bob.post("/ask", json={"question": "getopt?"}).status_code == 409  # Bob has nothing to search
    add(bob, "strtok splits strings", "bobs.pdf")
    answer = bob.post("/ask", json={"question": "tell me about getopt"}).json()
    assert answer["sources"] == []  # nothing relevant in Bob's OWN library
    sent_to_the_model = "\n".join(fake_gemini["prompts"])
    assert "confidential" not in sent_to_the_model  # the admin's text never reached the model


def test_the_same_file_name_in_two_libraries_stays_separate(client, new_user, fake_gemini):
    bob = new_user("bob")
    add(client, "getopt the admin version", "report.pdf")
    add(bob, "strtok the bob version", "report.pdf")
    bob.post("/ask", json={"question": "strtok?"})
    prompt = fake_gemini["prompts"][-1]
    assert "bob version" in prompt and "admin version" not in prompt


def test_jobs_are_private(client, new_user, fake_gemini):
    bob = new_user("bob")
    job = add(client, "getopt a", "a.pdf")
    assert bob.get("/jobs").json() == []
    # Not "forbidden": for Bob that job simply does not exist.
    assert bob.get(f"/jobs/{job['id']}").status_code == 404
    assert [j["id"] for j in client.get("/jobs").json()] == [job["id"]]


def test_removing_documents_never_touches_another_users(client, new_user, fake_gemini):
    bob = new_user("bob")
    add(client, "getopt a", "same-name.pdf")
    add(bob, "getopt b", "same-name.pdf")
    assert bob.delete("/documents", params={"source": "same-name.pdf"}).status_code == 200
    assert names(client) == ["same-name.pdf"]
    assert bob.delete("/documents/all").json() == {"removed_chunks": 0}
    assert names(client) == ["same-name.pdf"]


def test_a_user_cannot_delete_a_document_they_do_not_own(client, new_user, fake_gemini):
    bob = new_user("bob")
    add(client, "getopt a", "admins.pdf")
    assert bob.delete("/documents", params={"source": "admins.pdf"}).status_code == 404
    assert names(client) == ["admins.pdf"]


def test_being_an_administrator_does_not_open_other_peoples_documents(client, new_user, fake_gemini):
    bob = new_user("bob")
    add(bob, "strtok bobs private notes", "bobs.pdf")
    assert names(client) == []  # administrators manage accounts, they don't read documents
    assert client.get("/jobs").json() == []


def test_uploads_from_the_command_line_go_to_the_chosen_account(client, new_user, fake_gemini, tmp_path):
    bob = new_user("bob")
    pdf = tmp_path / "from-cli.pdf"
    pdf.write_bytes(make_pdf("getopt added from the command line"))
    assert ingest.main([str(pdf), "--user", "bob"]) == 0
    assert names(bob) == ["from-cli.pdf"] and names(client) == []


def test_the_command_line_refuses_an_unknown_account(client, fake_gemini, tmp_path, capsys):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(make_pdf("getopt"))
    assert ingest.main([str(pdf), "--user", "nobody"]) == 1
    assert "no account called" in capsys.readouterr().err
