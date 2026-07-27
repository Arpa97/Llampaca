import sys

with open("llampaca/gui/server.py", "r") as f:
    lines = f.readlines()

# Common Header
header = lines[0:22]
# Find split points
agent_manager_start = 23
agent_manager_end = -1
routes_start = -1
routes_end = -1

for i, line in enumerate(lines):
    if line.startswith("class QuietSimpleHTTPRequestHandler"):
        agent_manager_end = i
        routes_start = i
    if line.startswith("def find_free_port"):
        routes_end = i

print(f"AgentManager: {agent_manager_start} to {agent_manager_end}")
print(f"Routes: {routes_start} to {routes_end}")
print(f"Server: {routes_end} to end")

agent_lines = lines[agent_manager_start:agent_manager_end]
routes_lines = lines[routes_start:routes_end]
server_lines = lines[routes_end:]

# Clean up AgentManager lines
# Replace get_active_server / set_active_server with state
agent_text = "".join(agent_lines)
agent_text = agent_text.replace(
    "from llampaca.engine.server import get_active_server, LlamaServer, set_active_server",
    "from llampaca.engine.server import LlamaServer\n            from llampaca.engine import state"
)
agent_text = agent_text.replace(
    "from llampaca.engine.server import get_active_server, set_active_server",
    "from llampaca.engine import state"
)
agent_text = agent_text.replace("get_active_server()", "state.get_chat_server()")
agent_text = agent_text.replace("set_active_server(None)", "state.set_chat_server(None)")
agent_text = agent_text.replace("set_active_server(", "state.set_chat_server(")

with open("llampaca/gui/agent_manager.py", "w") as f:
    f.writelines(header)
    f.write("from llampaca.engine import state\n")
    f.write("from llampaca.engine.client import LlamaClient\n")
    f.write("from llampaca.engine.server import LlamaServer, _restart_active_server_fallback\n")
    f.write("from llampaca.tools import build_default_registry\n")
    f.write("from llampaca.config import MODELS_DIR\n")
    f.write("import time\nimport re\nimport uuid\n")
    f.write(agent_text)
    f.write("\nagent_manager = AgentManager()\n")

routes_text = "".join(routes_lines)
with open("llampaca/gui/routes.py", "w") as f:
    f.writelines(header)
    f.write("import urllib.parse\nimport io\nimport base64\nimport uuid\nimport shutil\nimport time\nimport re\n")
    f.write("from llampaca.gui.agent_manager import agent_manager, run_async\n")
    f.write("from llampaca.gui.restart_bridge import restart_via_agent_manager\n")
    f.write("from llampaca.config import MODELS_DIR, MODEL_PRESETS, save_config, get_app_dir\n")
    f.write(routes_text)

with open("llampaca/gui/server.py", "w") as f:
    f.writelines(header)
    f.write("from llampaca.gui.agent_manager import agent_manager\n")
    f.write("from llampaca.gui.routes import QuietSimpleHTTPRequestHandler, _get_client_config_paths, _resolve_llampaca_command, parse_hf_input, search_hf_models\n")
    f.writelines(server_lines)

print("Split completed successfully!")
