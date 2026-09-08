import click
import subprocess
import shutil
import json
import sys
from pathlib import Path
from llampaca.config import LLAMPACA_DIR, load_mcp_config, save_mcp_config

def run_generic_git_setup(slug: str, repo_url: str, env_vars_schema: dict):
    """Wizard generico per clonare, compilare e configurare qualsiasi server MCP basato su Git."""
    click.echo("\n" + "="*60)
    click.echo(f" WIZARD DI INSTALLAZIONE MCP: {slug}")
    click.echo("="*60)
    
    install_dir = LLAMPACA_DIR / "integrations" / slug
    install_dir.parent.mkdir(parents=True, exist_ok=True)
    
    # 1. Clone Git repository
    if install_dir.exists():
        if click.confirm(f"\nLa cartella '{install_dir}' esiste già. Vuoi aggiornarla (git pull)?", default=True):
            try:
                subprocess.run(["git", "pull"], cwd=str(install_dir), check=True)
            except Exception as e:
                click.echo(f"Errore durante l'aggiornamento (git pull): {e}")
    else:
        click.echo(f"\nClonazione del repository {repo_url}...")
        try:
            subprocess.run(["git", "clone", repo_url, str(install_dir)], check=True)
        except Exception as e:
            click.echo(f"Errore durante la clonazione git: {e}")
            return
            
    # 2. Detect project type
    is_node = (install_dir / "package.json").exists()
    is_python = (install_dir / "requirements.txt").exists() or (install_dir / "pyproject.toml").exists()
    
    cmd = None
    args = []
    
    if is_node:
        click.echo("\nRilevato progetto Node.js. Installazione dipendenze (npm install)...")
        try:
            subprocess.run(["npm", "install"], cwd=str(install_dir), check=True)
            
            package_json_path = install_dir / "package.json"
            with open(package_json_path, "r", encoding="utf-8") as f:
                pj = json.load(f)
            
            if "scripts" in pj and "build" in pj["scripts"]:
                click.echo("Esecuzione build (npm run build)...")
                subprocess.run(["npm", "run", "build"], cwd=str(install_dir), check=True)
                
        except Exception as e:
            click.echo(f"Errore durante la build di Node.js: {e}")
            return
            
        possible_entries = ["build/index.js", "dist/index.js", "index.js"]
        entry_file = None
        for pe in possible_entries:
            if (install_dir / pe).exists():
                entry_file = pe
                break
                
        if not entry_file:
            entry_file = click.prompt("Inserisci il file di ingresso relativo (es. build/index.js)", default="build/index.js")
            
        cmd = "node"
        args = [str(install_dir / entry_file)]
        
    elif is_python:
        click.echo("\nRilevato progetto Python. Creazione virtualenv (.venv)...")
        try:
            subprocess.run([sys.executable, "-m", "venv", str(install_dir / ".venv")], check=True)
            pip_path = install_dir / ".venv" / "bin" / "pip"
            
            if (install_dir / "requirements.txt").exists():
                subprocess.run([str(pip_path), "install", "-r", "requirements.txt"], cwd=str(install_dir), check=True)
            else:
                subprocess.run([str(pip_path), "install", "."], cwd=str(install_dir), check=True)
        except Exception as e:
            click.echo(f"Errore durante l'installazione delle dipendenze Python: {e}")
            return
            
        possible_entries = ["server.py", "main.py", "app.py", "index.py"]
        entry_file = None
        for pe in possible_entries:
            if (install_dir / pe).exists():
                entry_file = pe
                break
                
        if not entry_file:
            entry_file = click.prompt("Inserisci il file di ingresso Python (es. server.py)", default="server.py")
            
        cmd = str(install_dir / ".venv" / "bin" / "python")
        args = [str(install_dir / entry_file)]
        
    else:
        click.echo("\nImpossibile rilevare automaticamente il tipo di progetto (Node/Python).")
        cmd = click.prompt("Inserisci il comando da eseguire (es. python3, node)", type=str)
        args_str = click.prompt("Inserisci gli argomenti separati da spazi", type=str, default="")
        args = args_str.split() if args_str else []
        
    # 3. Environment variables configuration
    env = {}
    props = env_vars_schema.get("properties", {}) or {}
    required = env_vars_schema.get("required", []) or []
    
    if props:
        click.echo("\nConfigurazione Variabili d'Ambiente:")
        for var_name, var_info in props.items():
            desc = var_info.get("description", "")
            is_req = var_name in required
            req_str = " (Obbligatorio)" if is_req else " (Opzionale)"
            prompt_str = f"  {var_name}{req_str}"
            if desc:
                prompt_str += f"\n    Desc: {desc}\n  Valore"
            val = click.prompt(prompt_str, default="", show_default=False)
            if val.strip():
                env[var_name] = val.strip()
            elif is_req:
                click.echo(f"Errore: La variabile '{var_name}' è obbligatoria.")
                return
                
    # 4. Handle example config files
    example_configs = ["config.json.example", ".env.example"]
    for ec in example_configs:
        if (install_dir / ec).exists():
            target_name = ec.replace(".example", "")
            target_path = install_dir / target_name
            if not target_path.exists():
                click.echo(f"\nTrovato file di configurazione d'esempio: {ec}")
                if click.confirm(f"Vuoi copiarlo in {target_name}?", default=True):
                    shutil.copy(install_dir / ec, target_path)
                    click.echo(f"Copiato {ec} in {target_name}.")
                    click.echo(f"Puoi aprire e configurare il file qui: file://{target_path.resolve()}")
                    
    # 4b. Detect and run authentication scripts
    possible_auth_scripts = [
        "setup_auth.py", "auth.py", "authenticate.py", "login.py",
        "auth.js", "setup-auth.js", "login.js",
        "auth.sh", "setup-auth.sh"
    ]
    
    found_auth_script = None
    for pas in possible_auth_scripts:
        if (install_dir / pas).exists():
            found_auth_script = pas
            break
            
    if found_auth_script:
        click.echo(f"\nRilevato script di autenticazione: {found_auth_script}")
        if click.confirm("Desideri avviarlo ora per configurare/autenticare l'account?", default=True):
            try:
                if found_auth_script.endswith(".py"):
                    py_exec = str(install_dir / ".venv" / "bin" / "python") if is_python else sys.executable
                    subprocess.run([py_exec, found_auth_script], cwd=str(install_dir), check=True)
                elif found_auth_script.endswith(".js"):
                    subprocess.run(["node", found_auth_script], cwd=str(install_dir), check=True)
                elif found_auth_script.endswith(".sh"):
                    subprocess.run(["bash", found_auth_script], cwd=str(install_dir), check=True)
            except Exception as e:
                click.echo(f"Errore durante l'esecuzione dello script di autenticazione: {e}")
                
    # 5. Save config
    mcp_config = load_mcp_config()
    mcp_config["mcp_servers"][slug] = {
        "command": cmd,
        "args": args,
        "env": env
    }
    save_mcp_config(mcp_config)
    click.echo(f"\nIntegrazione '{slug}' configurata con successo in mcp_config.json!")
