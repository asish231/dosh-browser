//! CLI binary for dosh-browser-core.
//! Direct port of `webresearch/cli.py` & `webresearch/__main__.py`.

use std::env;
use std::process;
use dosh_browser_core::read::{read, JsMode};
use dosh_browser_core::search::search;
use dosh_browser_core::session::Session;

fn parse_shell_line(line: &str) -> (&str, serde_json::Value) {
    let raw = line.trim();
    if let Some((cmd, rest)) = raw.split_once(' ') {
        let rest = rest.trim();
        if rest.starts_with('{') {
            (cmd, serde_json::from_str(rest).unwrap_or(serde_json::json!({})))
        } else if rest.contains('=') && cmd != "eval" && cmd != "frame.eval" {
            let mut map = serde_json::Map::new();
            for token in rest.split_whitespace() {
                if let Some((k, v)) = token.split_once('=') {
                    if let Ok(num) = v.parse::<i64>() {
                        map.insert(k.to_string(), serde_json::json!(num));
                    } else if let Ok(b) = v.parse::<bool>() {
                        map.insert(k.to_string(), serde_json::json!(b));
                    } else {
                        map.insert(k.to_string(), serde_json::json!(v));
                    }
                }
            }
            if !map.is_empty() {
                (cmd, serde_json::Value::Object(map))
            } else {
                (cmd, serde_json::json!({ "value": rest }))
            }
        } else {
            match cmd {
                "open" => (cmd, serde_json::json!({ "url": rest })),
                "act" => (cmd, serde_json::json!({ "ref": rest })),
                "ref" => (cmd, serde_json::json!({ "stable": rest })),
                "tabs" => (cmd, serde_json::json!({ "id": rest })),
                "eval" => (cmd, serde_json::json!({ "source": rest })),
                "frame.eval" => (cmd, serde_json::json!({ "expr": rest })),
                "query" => (cmd, serde_json::json!({ "category": rest })),
                "slice" => (cmd, serde_json::json!({ "path": rest })),
                "wasm" => {
                    if let Ok(n) = rest.parse::<u64>() {
                        (cmd, serde_json::json!({ "fuel": n }))
                    } else {
                        (cmd, serde_json::json!({ "subcommand": rest }))
                    }
                }
                "clock" => {
                    if let Ok(n) = rest.parse::<u64>() {
                        (cmd, serde_json::json!({ "set": n }))
                    } else {
                        (cmd, serde_json::json!({ "subcommand": rest }))
                    }
                }
                "rng" => {
                    if let Ok(n) = rest.parse::<u64>() {
                        (cmd, serde_json::json!({ "seed": n }))
                    } else {
                        (cmd, serde_json::json!({ "seed": rest }))
                    }
                }
                "checkpoint" => (cmd, serde_json::json!({ "id": rest })),
                "restore" => (cmd, serde_json::json!({ "id": rest })),
                _ => (cmd, serde_json::json!({ "value": rest })),
            }
        }
    } else {
        (raw, serde_json::json!({}))
    }
}

fn find_python_executable() -> String {
    if let Ok(venv) = env::var("VIRTUAL_ENV") {
        let p = std::path::Path::new(&venv).join("bin/python3");
        if p.exists() {
            return p.to_string_lossy().to_string();
        }
        let p_alt = std::path::Path::new(&venv).join("bin/python");
        if p_alt.exists() {
            return p_alt.to_string_lossy().to_string();
        }
    }
    let local_venv = std::path::Path::new(".venv").join("bin/python3");
    if local_venv.exists() {
        return local_venv.to_string_lossy().to_string();
    }
    "python3".to_string()
}

#[tokio::main]
async fn main() {
    let raw_args: Vec<String> = env::args().collect();
    let mut is_python_mode = false;
    let mut args: Vec<String> = Vec::new();

    for (idx, a) in raw_args.into_iter().enumerate() {
        if idx == 0 {
            args.push(a);
            continue;
        }
        if a == "-p" || a == "--python" || a == "--py" {
            is_python_mode = true;
        } else if a == "-d" || a == "--default" || a == "--rust" {
            is_python_mode = false;
        } else {
            args.push(a);
        }
    }

    if is_python_mode {
        let py_bin = find_python_executable();
        let mut py_cmd = std::process::Command::new(py_bin);
        py_cmd.arg("-m").arg("webresearch");
        for a in args.iter().skip(1) {
            py_cmd.arg(a);
        }
        match py_cmd.status() {
            Ok(status) => {
                process::exit(status.code().unwrap_or(0));
            }
            Err(e) => {
                eprintln!("Error executing Python companion SDK: {}", e);
                eprintln!("\nTo install or activate the Python companion SDK:");
                eprintln!("    pip install dosh-browser");
                eprintln!("Or reinstall via:");
                eprintln!("    curl -fsSL https://raw.githubusercontent.com/asish231/dosh-browser/main/install.sh | bash -s -- -p");
                process::exit(1);
            }
        }
    }

    if args.len() < 2 {
        eprintln!("dosh — Agent-native browser (pure Rust core, doshgentv8 V8 engine)");
        eprintln!("Usage: dosh [-p|--python] [-d|--rust] <command> [args...]");
        eprintln!("\nLanes:");
        eprintln!("  -d, --rust, --default     Official standalone pure-Rust browser (default)");
        eprintln!("  -p, --py, --python        Optional Python companion SDK (webresearch)");
        eprintln!("\nCommands:");
        eprintln!("  read <url> [--json]       Read and project an LLM-ready page model");
        eprintln!("  search <query> [--json]   Web search with DuckDuckGo fallback");
        eprintln!("  download <url>            Download file or PDF streaming to disk with SHA-256");
        eprintln!("  ask <prompt>              Let an agent research and answer using browser tools");
        eprintln!("  shell [-c <command>]      Agent browser terminal (41 commands)");
        eprintln!("  server / serve [--port N] Run REST HTTP API & tool invoke endpoints");
        eprintln!("  mcp                       Run Model Context Protocol server over stdio");
        eprintln!("  product / status          Show official lane and engine status");
        process::exit(1);
    }

    let cmd = &args[1];
    match cmd.as_str() {
        "--help" | "-h" | "help" => {
            eprintln!("dosh — Agent-native browser (pure Rust core, doshgentv8 V8 engine)");
            eprintln!("Usage: dosh [-p|--python] [-d|--rust] <command> [args...]");
            eprintln!("\nLanes:");
            eprintln!("  -d, --rust, --default     Official standalone pure-Rust browser (default)");
            eprintln!("  -p, --py, --python        Optional Python companion SDK (webresearch)");
            eprintln!("\nCommands:");
            eprintln!("  read <url> [--json]       Read and project an LLM-ready page model");
            eprintln!("  search <query> [--json]   Web search with DuckDuckGo fallback");
            eprintln!("  download <url>            Download file or PDF streaming to disk with SHA-256");
            eprintln!("  ask <prompt>              Let an agent research and answer using browser tools");
            eprintln!("  shell [-c <command>]      Agent browser terminal (41 commands)");
            eprintln!("  server / serve [--port N] Run REST HTTP API & tool invoke endpoints");
            eprintln!("  mcp                       Run Model Context Protocol server over stdio");
            eprintln!("  product / status          Show official lane and engine status");
            process::exit(0);
        }
        "read" => {
            if args.len() < 3 {
                eprintln!("Usage: dosh read <url> [--json]");
                process::exit(1);
            }
            let url = &args[2];
            let as_json = args.iter().any(|a| a == "--json");
            match read(url, 8000, JsMode::Auto).await {
                Ok(page) => {
                    if as_json {
                        println!("{}", serde_json::to_string_pretty(&page).unwrap_or_default());
                    } else {
                        println!("{}", page.view);
                    }
                }
                Err(e) => {
                    eprintln!("Error reading {}: {}", url, e);
                    process::exit(1);
                }
            }
        }
        "search" => {
            if args.len() < 3 {
                eprintln!("Usage: dosh search <query> [--json]");
                process::exit(1);
            }
            let query = &args[2];
            let as_json = args.iter().any(|a| a == "--json");
            let client = reqwest::Client::new();
            match search(&client, query, 8, "auto").await {
                Ok(res) => {
                    if as_json {
                        println!("{}", serde_json::to_string_pretty(&res).unwrap_or_default());
                    } else {
                        println!("{}", res.view);
                    }
                }
                Err(e) => {
                    eprintln!("Error searching {}: {}", query, e);
                    process::exit(1);
                }
            }
        }
        "download" => {
            if args.len() < 3 {
                eprintln!("Usage: dosh download <url> [--expected <hash>]");
                process::exit(1);
            }
            let url = &args[2];
            let expected = args.iter().position(|a| a == "--expected")
                .and_then(|idx| args.get(idx + 1).map(|s| s.as_str()));
            let client = reqwest::Client::new();
            let rec = dosh_browser_core::download::download(&client, url, expected, None, 50_000_000).await;
            println!("{}", serde_json::to_string_pretty(&rec).unwrap_or_default());
        }
        "ask" => {
            if args.len() < 3 {
                eprintln!("Usage: dosh ask <prompt>");
                process::exit(1);
            }
            let prompt = &args[2];
            let client = reqwest::Client::new();
            let session = std::sync::Arc::new(tokio::sync::Mutex::new(Session::new(client)));
            match dosh_browser_core::agent::ask(prompt, session).await {
                Ok(res) => {
                    println!("{}", res.answer);
                }
                Err(e) => {
                    eprintln!("Agent error: {}", e);
                    process::exit(1);
                }
            }
        }
        "shell" => {
            let client = reqwest::Client::new();
            let mut session = Session::new(client);
            let db_path = args.iter().position(|a| a == "--database")
                .and_then(|idx| args.get(idx + 1).map(|s| s.as_str()));
            let store = dosh_browser_core::shell::ShellStore::new(db_path.map(std::path::PathBuf::from)).expect("Failed to initialize shell store");
            
            // Collect all -c commands
            let mut c_commands = Vec::new();
            let mut i = 0;
            while i < args.len() {
                if args[i] == "-c" && i + 1 < args.len() {
                    c_commands.push(args[i + 1].clone());
                    i += 2;
                } else {
                    i += 1;
                }
            }

            if !c_commands.is_empty() {
                for cmd_str in c_commands {
                    let (exec_cmd, shell_args) = parse_shell_line(&cmd_str);
                    match dosh_browser_core::shell::execute_command(exec_cmd, &shell_args, &mut session, &store).await {
                        Ok(val) => println!("{}", serde_json::to_string(&val).unwrap_or_default()),
                        Err(e) => eprintln!("Error: {}", e),
                    }
                }
                return;
            }

            // Interactive stdin loop
            use std::io::BufRead;
            let stdin = std::io::stdin();
            for line in stdin.lock().lines() {
                if let Ok(l) = line {
                    let trimmed = l.trim();
                    if trimmed.is_empty() {
                        continue;
                    }
                    if trimmed == "exit" || trimmed == "quit" {
                        break;
                    }
                    let (exec_cmd, shell_args) = parse_shell_line(trimmed);
                    match dosh_browser_core::shell::execute_command(exec_cmd, &shell_args, &mut session, &store).await {
                        Ok(val) => println!("{}", serde_json::to_string(&val).unwrap_or_default()),
                        Err(e) => eprintln!("Error: {}", e),
                    }
                }
            }
        }
        "server" | "serve" => {
            let port: u16 = if let Some(pos) = args.iter().position(|a| a == "--port") {
                args.get(pos + 1).and_then(|p| p.parse().ok()).unwrap_or(4747)
            } else {
                4747
            };
            let addr = format!("127.0.0.1:{}", port);
            println!("Starting dosh HTTP server on {}...", addr);
            let store = dosh_browser_core::shell::ShellStore::new(None).expect("Failed to initialize shell store");
            let state = dosh_browser_core::server::AppState::new(store);
            let app = dosh_browser_core::server::app(state);
            let listener = tokio::net::TcpListener::bind(&addr)
                .await
                .unwrap_or_else(|_| panic!("Failed to bind to {}", addr));
            axum::serve(listener, app).await.expect("Server failed");
        }
        "mcp" => {
            let client = reqwest::Client::new();
            let session = std::sync::Arc::new(tokio::sync::Mutex::new(Session::new(client)));
            let store = std::sync::Arc::new(dosh_browser_core::shell::ShellStore::new(None).expect("Failed to init store"));
            use std::io::BufRead;
            let stdin = std::io::stdin();
            for line in stdin.lock().lines() {
                if let Ok(l) = line {
                    if l.trim().is_empty() {
                        continue;
                    }
                    if let Ok(req) = serde_json::from_str(&l) {
                        if let Some(resp) = dosh_browser_core::mcp::handle_mcp_message(req, &session, &store).await {
                            println!("{}", serde_json::to_string(&resp).unwrap_or_default());
                        }
                    }
                }
            }
        }
        "product" | "status" => {
            let status = dosh_browser_core::config::project_status();
            println!("{}", serde_json::to_string_pretty(&status).unwrap_or_default());
        }
        _ => {
            eprintln!("Unknown command: {}", cmd);
            process::exit(1);
        }
    }
}
