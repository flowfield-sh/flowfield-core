"""Serve a retained, model-free Pocket Garden UI trial with explicitly simulated workers.

Creates only a new directory. Reuse an existing demo with --resume; never resets state.
Real service ownership, questions, checks, inspection and delivery; scripted agent behavior.
"""

import argparse
import asyncio
import json
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import uvicorn

from flowfield import supervisor as worker_module
from flowfield.adapters.git_workspace import git
from flowfield.adapters.local_execution import LocalHost
from flowfield.agent_models import AgentChoice
from flowfield.api import create_app
from flowfield.application import MilestoneCreate, ProjectSetup, TaskCreate, TaskPublish, Workspace
from flowfield.execution import Execution
from flowfield.execution_models import ModelOption, QueueEdit, SettingsEdit, WorkerResult
from flowfield.inspection import Inspections
from flowfield.inspection_models import InspectionConfig
from flowfield.integration import Integrations
from flowfield.integration_models import IntegrationConfig
from flowfield.questions import QuestionCreate
from flowfield.result_models import ResultReview
from flowfield.results import Results
from flowfield.run_activity import ActivityUpdate, RunActivity
from flowfield.stage_models import Stage, StageUpdate
from flowfield.stages import Stages

HTML = """<!doctype html><html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Pocket Garden</title>
<style>body{font:18px system-ui;max-width:650px;margin:60px auto;padding:20px;
color:#243c2e;background:#f7faf5}
button,input{font:inherit;padding:10px}li{padding:12px 0}h1{font-size:36px}</style>
<h1>Pocket Garden</h1><p>A small place to remember your plants.</p><form>
<input aria-label="Plant name" placeholder="Plant name" required>
<button>Add plant</button></form><ul></ul>
<script>let plants=JSON.parse(localStorage.getItem('plants')||'["Basil","Monstera"]');
function render(){
document.querySelector('ul').replaceChildren(...plants.map(name=>{
let li=document.createElement('li');
li.textContent=name;return li}));
localStorage.setItem('plants',JSON.stringify(plants))}
document.querySelector('form').onsubmit=e=>{e.preventDefault();
let i=document.querySelector('input');plants.push(i.value);i.value='';render()};
render();</script></html>"""


def demo_stages(task_type, active=-1):
    investigation = task_type == "investigation"
    return [
        Stage(
            id=name,
            title=title,
            outcome=outcome,
            status="completed" if i < active else "active" if i == active else "planned",
        )
        for i, (name, title, outcome) in enumerate(
            [
                (
                    "understand",
                    "Understand",
                    "Confirm the requested experience and constraints.",
                ),
                (
                    "build",
                    "Explore" if investigation else "Build",
                    "Gather evidence and alternatives."
                    if investigation
                    else "Implement the agreed small outcome.",
                ),
                ("check", "Check", "Verify behavior and report remaining limitations."),
            ]
        )
    ]


def plan(workspace, task, active=0):
    current = Stages(workspace).get(task.project_id, task.id)
    return Stages(workspace).update(
        task.project_id,
        task.id,
        StageUpdate(
            expected_revision=current.revision,
            agreement_revision=task.agreement_revision,
            stages=demo_stages(task.task_type, active),
            reason="Demo progress plan; the agreed outcome still governs completion.",
        ),
    )


def seed(root):
    root.mkdir(parents=True)
    repo = root / "pocket-garden"
    repo.mkdir()
    git(repo, "init")
    (repo / "index.html").write_text(HTML)
    (repo / "server.mjs").write_text(
        "import {createServer} from 'node:http';\nimport {readFileSync} from 'node:fs';\n"
        "createServer((req,res)=>{res.setHeader('Content-Type','text/html; charset=utf-8');"
        "res.end(readFileSync(new "
        "URL('./index.html',import.meta.url)))}).listen(8788,'127.0.0.1');\n"
        "console.log('Pocket Garden: http://127.0.0.1:8788');\n"
    )
    (repo / "test.mjs").write_text(
        "import {test} from 'node:test';import assert from 'node:assert/strict';"
        "import {readFileSync} from 'node:fs';test('plant entry remains available',()=>{"
        "const html=readFileSync('index.html','utf8');assert.match(html,/Plant name/);"
        "assert.match(html,/localStorage/)});\n"
    )
    (repo / "README.md").write_text(
        "# Pocket Garden\n\nScripted Flowfield UI trial; no model calls.\n"
    )
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Demo",
        "-c",
        "user.email=demo@example.invalid",
        "commit",
        "-m",
        "Toy project baseline",
    )
    ws = Workspace(root / "state")
    project = ws.setup_project(
        ProjectSetup(path=str(repo), name="Pocket Garden · simulated workers", task_prefix="GRD")
    )
    # Registration adds project identity; include it in the clean fixture baseline.
    git(repo, "add", ".flowfield")
    git(
        repo,
        "-c",
        "user.name=Demo",
        "-c",
        "user.email=demo@example.invalid",
        "commit",
        "-m",
        "Register toy project",
    )
    execution = Execution(ws)
    node = str(Path(shutil.which("node")).resolve())
    Integrations(ws).configure(
        project.id,
        IntegrationConfig(
            runtime="local",
            expected_revision=1,
            target_branch=git(repo, "branch", "--show-current").decode().strip(),
            checks=[f"{node} --test test.mjs"],
        ),
    )
    Inspections(ws).configure(
        project.id, InspectionConfig(expected_revision=1, run_command=f"{node} server.mjs")
    )
    execution.configure(
        project.id,
        SettingsEdit(
            expected_revision=1, selection=AgentChoice(model="demo-scripted", effort="none")
        ),
    )
    execution.queue(
        project.id,
        QueueEdit(expected_revision=execution.settings(project.id).revision, enabled=True),
    )
    milestone = ws.create_milestone(
        project.id,
        MilestoneCreate(
            title="A useful little plant journal",
            body="Add plants, understand watering, and keep the experience small.",
        ),
    )

    def task(identity, title, body, completion="code", **kwargs):
        item = ws.create_task(
            project.id,
            TaskCreate(
                stages=demo_stages(kwargs.get("task_type", "feature")),
                id=identity,
                title=title,
                body=body,
                status=kwargs.pop("status", "up_next"),
                milestone_id=milestone.id,
                **kwargs,
            ),
        )
        if item.status != "backlog":
            item = ws.publish_task(
                project.id,
                item.id,
                TaskPublish(
                    expected_revision=item.revision,
                    completion=completion,
                ),
            )
            plan(ws, item)
        return item

    def capture(item, text, delivered=False):
        head = Integrations(ws).head(project.id)
        run = execution.claim(project.id, head, {head: set()})
        assert run and run.task_id == item.id
        env = LocalHost(os.environ).prepare(root / "state", repo, run.id, run.base_commit)
        execution.save_local(
            run.id,
            {
                "runtime_kind": "local",
                **{
                    key: str(getattr(env, key))
                    for key in ("root", "checkout", "runtime", "common_git")
                },
            },
        )
        execution.started(project.id, run.id)
        RunActivity(ws).write(
            project.id,
            run.id,
            [
                ActivityUpdate(
                    key="read", kind="agent", text="Simulated worker: inspecting the plant journal."
                ),
                ActivityUpdate(key="check", kind="command", text=f"{node} --test test.mjs"),
                ActivityUpdate(
                    key="report",
                    kind="status",
                    text="The service validates the candidate separately.",
                ),
            ],
        )
        (env.checkout / f"{item.id}.md").write_text(text)
        commit, _ = env.snapshot(run.base_commit)
        execution.finish(
            project.id,
            run.id,
            "in_review",
            commit=commit,
            result=WorkerResult(
                summary=text,
                checks="Scripted fixture report; see service-observed checks.",
                limitations="Simulated worker, not a model-generated implementation.",
            ),
        )
        results = Results(ws)
        results.process(project.id)
        version = results.page(project.id, item.id).items[0]
        assert version.status == "ready", version.problem
        plan(ws, ws.task(project.id, item.id), 3 if delivered else 2)
        if delivered:
            results.review(
                project.id,
                version.id,
                ResultReview(
                    expected_revision=version.revision,
                    candidate_commit=version.candidate_commit,
                    action="approve",
                    author="human",
                ),
            )
            results.process(project.id)
            assert ws.task(project.id, item.id).status == "done"

    first = task(
        "journal",
        "Keep a simple plant journal",
        "Add plants by name and retain them locally. Keep the interface small.",
    )
    capture(first, "Plant entries stay in this browser. The toy app is ready to try.", True)
    second = task(
        "watering",
        "Explain when plants need water",
        "Document a simple watering flow without pretending every plant follows a fixed schedule.",
    )
    capture(
        second,
        "Watering guidance distinguishes checking soil from a fixed "
        "calendar. Review this proposed guide.",
    )
    choice = task(
        "reminders",
        "Choose how reminders should behave",
        "Decide whether reminders are passive hints or explicit browser notifications.",
    )
    head = Integrations(ws).head(project.id)
    run = execution.claim(project.id, head, {head: set()})
    execution.started(project.id, run.id)
    execution.ask_question(
        project.id,
        run.id,
        QuestionCreate(
            task_id=choice.id,
            question="Should watering reminders be quiet hints or browser notifications?",
            context="The first version is local-only. Notifications would require "
            "explicit permission.",
            recommendation="Start with quiet hints; add notifications only after trying the "
            "routine.",
            choices=["Quiet hints", "Opt-in notifications"],
            blocking_scope="Reminder behavior",
            author="worker",
        ),
    )
    execution.finish(project.id, run.id, "waiting_for_input", input_checkpoint=head)
    task(
        "research",
        "Investigate accessible watering controls",
        "Inspect keyboard and screen-reader needs; deliver findings, "
        "uncertainty and a recommendation.",
        completion="report",
        task_type="investigation",
    )
    task(
        "backup",
        "Describe a lightweight backup format",
        "Recommend a small export format. Deliver a report only.",
        completion="report",
        task_type="investigation",
    )
    task(
        "reminder-copy",
        "Write reminder copy after the behavior is chosen",
        "Use the chosen reminder behavior and keep the wording concrete.",
        dependencies=[choice.id],
    )
    task(
        "photos",
        "Explore plant photos",
        "A speculative idea: photos might help identify plants. No commitment yet.",
        status="backlog",
    )
    execution.queue(
        project.id,
        QueueEdit(expected_revision=execution.settings(project.id).revision, enabled=False),
    )
    (root / "demo.json").write_text(json.dumps({"project": project.id, "node": node}))


class DemoWorker:
    """A deterministic adapter used only by this explicit demo process."""

    supports_activity = True
    binary = Path("/usr/bin/true")

    def __init__(self, directory, cwd, environment):
        self.cwd = cwd
        self.cleanup_confirmed = True
        self.process = None
        self.on_tool = self.on_usage = self.on_commands = self.on_activity = None
        self.stopping = False

    async def start(self, servers):
        from contextlib import AsyncExitStack

        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        self.stack = AsyncExitStack()
        server = servers[0]
        client = await self.stack.enter_async_context(
            httpx.AsyncClient(headers={h.name: h.value for h in server.headers}, trust_env=False)
        )
        read, write, _ = await self.stack.enter_async_context(
            streamable_http_client(server.url, http_client=client)
        )
        session = await self.stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        async def call(name, arguments):
            result = await session.call_tool(name, arguments)
            assert not result.isError, result
            return result.content[0].text

        self.on_tool = call
        self.tools = [{"name": tool.name} for tool in (await session.list_tools()).tools]
        self.session = SimpleNamespace(session_id="fixture-" + self.cwd.parent.name)
        self.process = SimpleNamespace(pid=os.getpid())

    async def configure(self, choice, *, discussion=False):
        self.choice = choice
        return choice

    async def prompt(self, text, on_permission):
        return await self.run(self.choice.model, self.choice.effort, text, self.tools)

    async def run(self, model, effort, prompt, tools):
        brief = json.loads(prompt)
        context = json.loads(await self.on_tool("read_context", {"section": "description"}))
        del context
        discussion = "read-only discussion" in brief["instructions"]
        progress = None
        if not discussion:
            source = json.loads(await self.on_tool("read_context", {"section": "stages"}))
            progress = json.loads(source["text"])
            if progress.get("stages"):
                for index, stage in enumerate(progress["stages"]):
                    stage["status"] = (
                        "completed" if index == 0 else "active" if index == 1 else "planned"
                    )
                progress = json.loads(
                    await self.on_tool(
                        "update_stages",
                        {
                            "expected_revision": progress["revision"],
                            "agreement_revision": progress["agreement_revision"],
                            "stages": progress["stages"],
                            "reason": "Simulated worker started the focused inspection.",
                        },
                    )
                )
        duration = 180 if "Investigate accessible" in brief["task"] and not discussion else 12
        messages = [
            "Reading the agreed outcome and existing controls.",
            "Checking keyboard entry and focus behavior.",
            "Considering reminders without relying on color alone.",
            "Collecting findings and remaining questions.",
        ]
        for index in range(duration // 3):
            if self.stopping:
                return {"status": "interrupted"}
            if self.on_activity:
                self.on_activity(
                    ActivityUpdate(
                        key=f"demo-{index}",
                        kind="agent" if index % 3 == 0 else "status",
                        text="Simulated worker · " + messages[(index // 4) % len(messages)],
                    )
                )
            await asyncio.sleep(3)
        if not discussion and brief.get("completion") == "code":
            (self.cwd / "reminder-notes.md").write_text(
                "# Reminder behavior\n\nDemo continuation recorded. This scripted "
                "worker does not implement arbitrary requests.\n"
            )
        if progress and progress.get("stages"):
            for stage in progress["stages"]:
                stage["status"] = "completed"
            await self.on_tool(
                "update_stages",
                {
                    "expected_revision": progress["revision"],
                    "agreement_revision": progress["agreement_revision"],
                    "stages": progress["stages"],
                    "reason": "Scripted demo findings are ready; no model competence claim.",
                },
            )
        await self.on_tool(
            "submit_result",
            {
                "summary": "Simulated reply: the toy app keeps plant names in local browser "
                "storage."
                if discussion
                else "Keyboard entry uses a labelled input and native button. Keep "
                "reminders optional and verify with a screen reader.",
                "checks": "Scripted demo observations; no live model reasoning.",
                "outcome": "complete",
                "limitations": "This demo uses a scripted worker. Arbitrary feedback is preserved "
                "but not intelligently implemented.",
            },
        )
        return {"status": "completed"}

    async def stop(self):
        self.stopping = True
        return True

    async def close(self):
        await self.stack.aclose()


async def demo_models(*args):
    return [
        ModelOption(id="demo-scripted", name="Simulated worker · no model calls", efforts=["none"])
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    root = args.directory.expanduser().resolve()
    if root.exists():
        if not args.resume or not (root / "demo.json").exists():
            parser.error("Choose a new directory, or --resume an existing demo; nothing was reset.")
    else:
        seed(root)
    worker_module.CodexAgent = DemoWorker
    worker_module.model_options = demo_models
    worker_module.process_stamp = lambda pid: "simulated-demo-process"
    app = create_app(data_dir=root / "state")
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with original(app):
            project = json.loads((root / "demo.json").read_text())["project"]
            execution = app.state.supervisor.execution
            execution.queue(
                project,
                QueueEdit(expected_revision=execution.settings(project).revision, enabled=True),
            )
            yield

    app.router.lifespan_context = lifespan
    print(f"UI trial: http://127.0.0.1:{args.port}/projects/pocket-garden", flush=True)
    print(f"Retained state: {root}; simulated workers only.", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
