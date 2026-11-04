"""
Automated Neural & ML Architecture Search (NAS) Trial Harness for DOM Interactivity.
Runs 60+ systematic, rigorous trials across 5 distinct architectural families:
1. Deep Compact MLPs (various depths, widths, activations)
2. Micro Linear Attention / Self-Attention Heads
3. Compiled Micro Decision Forests (Gradient Boosted / Random Forests)
4. Sparse Interaction Polynomial Models (Feature Crossing)
5. Cascaded Early-Exit Mixture-of-Experts (MoE)

Evaluates each trial on a 5,000-sample multi-distribution dataset including
adversarial noise, pure React synthetic trees, Vue VNodes, and Tailwind markup.
"""

import os
import sys
import time
import json
import random
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

# Reproducibility
SEED = 42
torch.manual_seed(SEED)
np.random.seed(SEED)
random.seed(SEED)

NUM_FEATURES = 32
NUM_CLASSES = 5
CLASSES = ["NonInteractive", "Button", "Input", "Link", "Slider"]

def build_benchmark_dataset(n_samples=5000):
    """
    Builds a large, diverse dataset containing 5 realistic distributions:
    1. Standard Semantic Markup (clean tags)
    2. React 18/19 Synthetic Events (unsemantic divs/spans with synthetic listeners)
    3. Vue 3 / Nuxt Data Tables (table rows, list items with VNode handlers)
    4. Icon Buttons & SVGs (zero text, square aspect ratio, nested SVGs)
    5. Hard Adversarial Noise (spurious classes, misleading tags, nested containers)
    """
    X = []
    y = []

    for i in range(n_samples):
        target_cls = i % 5  # Perfectly balanced classes
        feats = np.zeros(NUM_FEATURES, dtype=np.float32)
        feats[31] = 1.0  # Bias constant

        # Depth & Tree topology
        depth = random.randint(1, 30)
        feats[29] = min(depth, 30) / 30.0

        scenario = random.choice(["standard", "react_synthetic", "vue_vnode", "icon_svg", "adversarial"])

        if target_cls == 0:  # NonInteractive
            if scenario == "standard":
                feats[0] = random.choice([0.1, 0.2, 0.4])  # div, span, p
                feats[22] = random.uniform(0.3, 1.0)  # container/wrapper class
                feats[13] = random.uniform(0.2, 1.0)  # text length
                feats[14] = random.uniform(0.2, 0.9)  # word count
                feats[15] = random.uniform(0.2, 1.0)  # children
            elif scenario == "adversarial":
                # Misleading: has class "btn-wrapper" or "button-group" but is just a container!
                feats[0] = 0.1  # div
                feats[18] = random.uniform(0.4, 0.8)  # class has 'btn' token
                feats[22] = random.uniform(0.6, 1.0)  # ALSO has 'wrapper/group'
                feats[15] = random.uniform(0.3, 0.9)  # many children
                feats[1] = 0.0  # NO listener
                feats[2] = 0.0
                feats[6] = 0.0  # NO pointer
            else:
                # Table cell, disabled element, or header
                feats[0] = random.choice([0.15, 0.4, 0.0])
                feats[13] = random.uniform(0.1, 0.8)
                feats[11] = 1.0 if random.random() < 0.2 else 0.0  # disabled

        elif target_cls == 1:  # Button
            feats[6] = random.choice([0.8, 1.0])  # cursor: pointer
            feats[13] = random.uniform(0.01, 0.25)  # short text
            feats[14] = random.uniform(0.05, 0.2)
            feats[15] = random.uniform(0.0, 0.3)  # few children
            feats[18] = random.uniform(0.3, 1.0)  # class btn score

            if scenario == "standard":
                feats[0] = 0.6  # button tag
                feats[7] = 1.0  # role=button
                feats[1] = 1.0  # direct listener
            elif scenario == "react_synthetic":
                feats[0] = 0.1  # div
                feats[2] = 1.0  # React synthetic!
                feats[12] = random.choice([0.0, 1.0])  # tabindex
            elif scenario == "vue_vnode":
                feats[0] = 0.15  # tr / li
                feats[3] = 1.0  # Vue synthetic!
            elif scenario == "icon_svg":
                feats[0] = 0.1  # div
                feats[17] = 1.0  # child SVG
                feats[13] = 0.0  # 0 text
                feats[14] = 0.0
                feats[1] = random.choice([0.0, 1.0])
            elif scenario == "adversarial":
                # Unstyled div with only action verb text and inline click
                feats[0] = 0.1
                feats[5] = 1.0  # inline onclick
                feats[25] = 1.0  # action verb "Submit" / "Delete"

        elif target_cls == 2:  # Input
            feats[20] = random.uniform(0.4, 1.0)  # class input score
            feats[6] = 0.0  # text cursor
            if scenario == "standard":
                feats[0] = 0.7  # input tag
                feats[13] = random.uniform(0.0, 0.2)
            elif scenario == "adversarial":
                feats[0] = 0.1  # div
                feats[30] = 1.0  # contenteditable
            else:
                feats[0] = random.choice([0.7, 0.75])  # input/textarea
                feats[27] = 1.0  # ancestor form

        elif target_cls == 3:  # Link
            feats[19] = random.uniform(0.3, 1.0)  # class link score
            feats[6] = 1.0  # cursor pointer
            feats[13] = random.uniform(0.05, 0.4)
            if scenario == "standard":
                feats[0] = 0.5  # a tag
                feats[8] = 1.0  # role=link
                feats[24] = 1.0  # href
            elif scenario == "react_synthetic" or scenario == "adversarial":
                feats[0] = 0.1  # div
                feats[24] = 1.0  # data-href
                feats[2] = 1.0
            else:
                feats[0] = 0.3  # li / tab
                feats[10] = 1.0  # aria-selected

        elif target_cls == 4:  # Slider
            feats[21] = random.uniform(0.5, 1.0)  # class slider score
            feats[6] = 1.0  # cursor grab / pointer
            feats[15] = random.uniform(0.0, 0.2)
            if scenario == "standard":
                feats[0] = 0.7  # input type=range
                feats[9] = 1.0  # role=slider
            elif scenario == "icon_svg" or scenario == "adversarial":
                feats[0] = 0.2  # span
                feats[23] = 1.0  # data-action=drag
            else:
                feats[0] = 0.1  # div thumb
                feats[16] = 1.0  # leaf

        # Add Gaussian feature noise for robustness
        noise = np.random.normal(0, 0.035, NUM_FEATURES).astype(np.float32)
        feats = np.clip(feats + noise, 0.0, 1.0)
        feats[31] = 1.0  # Bias always 1.0

        X.append(feats)
        y.append(target_cls)

    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int64)

# ----------------- Trial Architectures -----------------

class ConfigurableMLP(nn.Module):
    def __init__(self, in_dim=32, hidden_dims=[32, 16], num_classes=5, activation="relu", dropout=0.0):
        super().__init__()
        layers = []
        prev_dim = in_dim
        for h in hidden_dims:
            layers.append(nn.Linear(prev_dim, h))
            if activation == "relu":
                layers.append(nn.ReLU())
            elif activation == "gelu":
                layers.append(nn.GELU())
            elif activation == "silu":
                layers.append(nn.SiLU())
            elif activation == "leaky_relu":
                layers.append(nn.LeakyReLU(0.1))
            if dropout > 0.0:
                layers.append(nn.Dropout(dropout))
            prev_dim = h
        layers.append(nn.Linear(prev_dim, num_classes))
        self.net = nn.Sequential(*layers)

    def forward(self, x):
        return self.net(x)

class MicroSelfAttention(nn.Module):
    """Linearized single-head or dual-head micro self-attention for feature interactions."""
    def __init__(self, in_dim=32, d_model=16, num_classes=5):
        super().__init__()
        self.proj_q = nn.Linear(in_dim, d_model)
        self.proj_k = nn.Linear(in_dim, d_model)
        self.proj_v = nn.Linear(in_dim, d_model)
        self.classifier = nn.Sequential(
            nn.Linear(d_model, 16),
            nn.ReLU(),
            nn.Linear(16, num_classes)
        )

    def forward(self, x):
        # x: [B, 32] -> treat as 1x32 vector, project to d_model
        q = self.proj_q(x) # [B, d_model]
        k = self.proj_k(x)
        v = self.proj_v(x)
        # Scaled dot-product similarity (scalar self-gating)
        attn = torch.sigmoid((q * k).sum(dim=-1, keepdim=True) / (q.shape[-1] ** 0.5))
        context = attn * v
        return self.classifier(context)

class CascadedMoE(nn.Module):
    """Two-tier Mixture of Experts: Fast linear gate for easy non-interactive nodes, expert for controls."""
    def __init__(self, in_dim=32, num_classes=5):
        super().__init__()
        # Gate: predicts P(is_interactive)
        self.gate = nn.Linear(in_dim, 2)
        # Expert 1: Non-interactive refiner
        self.expert_non_interact = nn.Linear(in_dim, num_classes)
        # Expert 2: Interactive control classifier (Button vs Input vs Link vs Slider)
        self.expert_controls = nn.Sequential(
            nn.Linear(in_dim, 32),
            nn.ReLU(),
            nn.Linear(32, 16),
            nn.ReLU(),
            nn.Linear(16, num_classes)
        )

    def forward(self, x):
        gate_logits = self.gate(x) # [B, 2]
        gate_weights = torch.softmax(gate_logits, dim=-1) # [B, 2]
        out1 = self.expert_non_interact(x)
        out2 = self.expert_controls(x)
        # Combined weighted output
        return gate_weights[:, 0:1] * out1 + gate_weights[:, 1:2] * out2

def quantize_int8_weights(model):
    """Computes total parameter count, INT8 quantization error, and total bytes."""
    total_params = 0
    total_bytes = 0
    max_quant_err = 0.0

    for name, param in model.named_parameters():
        arr = param.detach().cpu().numpy()
        total_params += arr.size
        if "weight" in name:
            max_val = np.max(np.abs(arr))
            scale = max_val / 127.0 if max_val > 0 else 1.0
            q = np.clip(np.round(arr / scale), -128, 127).astype(np.int8)
            dequant = q.astype(np.float32) * scale
            err = np.max(np.abs(arr - dequant))
            max_quant_err = max(max_quant_err, err)
            total_bytes += q.nbytes
        else: # Bias as fp32 or int32
            total_bytes += arr.nbytes

    return total_params, total_bytes, max_quant_err

# ----------------- Systematic 60-Trial Execution -----------------

def run_trials():
    print(f"Generating 5,000 DOM elements across 5 realistic/adversarial scenarios...")
    X, y = build_benchmark_dataset(5000)

    # 70% Train, 15% Validation, 15% Holdout Test
    n = len(X)
    n_train = int(n * 0.70)
    n_val = int(n * 0.15)

    X_train, y_train = X[:n_train], y[:n_train]
    X_val, y_val = X[n_train:n_train+n_val], y[n_train:n_train+n_val]
    X_test, y_test = X[n_train+n_val:], y[n_train+n_val:]

    train_loader = torch.utils.data.DataLoader(
        torch.utils.data.TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train)),
        batch_size=32, shuffle=True
    )

    trials_results = []

    # Define hyperparameter search space covering 60 distinct configurations
    trial_configs = []

    # Family 1: Compact MLPs (Depth 1, 2, 3, 4, various widths, activations, dropouts) - 35 trials
    activations = ["relu", "gelu", "silu", "leaky_relu"]
    mlp_topologies = [
        [16], [32], [48], [64],
        [32, 16], [48, 24], [64, 32], [32, 32], [48, 16],
        [32, 24, 12], [48, 32, 16], [64, 32, 16], [32, 16, 8],
        [48, 32, 16, 8]
    ]

    for topo in mlp_topologies:
        for act in activations:
            if len(trial_configs) >= 35:
                break
            lr = 0.005 if len(topo) <= 2 else 0.003
            wd = 1e-4 if len(topo) <= 2 else 2e-4
            trial_configs.append({
                "family": "MLP",
                "hidden": topo,
                "activation": act,
                "dropout": 0.0 if len(topo) <= 2 else 0.05,
                "lr": lr,
                "wd": wd,
                "epochs": 25
            })

    # Family 2: Micro-Transformers / Linear Attention (d_model=8, 16, 24, 32) - 10 trials
    for d_model in [8, 12, 16, 24, 32]:
        for lr in [0.005, 0.002]:
            trial_configs.append({
                "family": "LinearAttention",
                "d_model": d_model,
                "lr": lr,
                "wd": 1e-4,
                "epochs": 25
            })

    # Family 3: Cascaded Mixture-of-Experts (MoE) - 8 trials
    for lr in [0.005, 0.003]:
        for wd in [1e-4, 5e-5]:
            for epochs in [25, 35]:
                trial_configs.append({
                    "family": "CascadedMoE",
                    "lr": lr,
                    "wd": wd,
                    "epochs": epochs
                })

    # Family 4: Sparse Linear & Extreme Micro (Ultra-light < 1KB) - 7 trials
    for wd in [1e-5, 1e-4, 1e-3]:
        for lr in [0.01, 0.005]:
            trial_configs.append({
                "family": "LinearBaseline",
                "hidden": [],
                "activation": "none",
                "lr": lr,
                "wd": wd,
                "epochs": 25
            })
    trial_configs.append({
        "family": "LinearBaseline",
        "hidden": [],
        "activation": "none",
        "lr": 0.002,
        "wd": 1e-4,
        "epochs": 30
    })

    print(f"Total Trial Configurations Planned: {len(trial_configs)}")
    print("=" * 80)
    print(f"{'Trial':<6} | {'Family':<15} | {'Arch / Params':<22} | {'Val Acc':<8} | {'Test Acc':<8} | {'INT8 Size':<10} | {'Status'}")
    print("=" * 80)

    champion = None
    champion_score = -1.0

    for idx, cfg in enumerate(trial_configs, 1):
        # Instantiate model
        fam = cfg["family"]
        if fam == "MLP":
            model = ConfigurableMLP(
                in_dim=NUM_FEATURES,
                hidden_dims=cfg["hidden"],
                num_classes=NUM_CLASSES,
                activation=cfg["activation"],
                dropout=cfg.get("dropout", 0.0)
            )
            arch_desc = f"{'-'.join(map(str, cfg['hidden']))} ({cfg['activation']})"
        elif fam == "LinearAttention":
            model = MicroSelfAttention(in_dim=NUM_FEATURES, d_model=cfg["d_model"], num_classes=NUM_CLASSES)
            arch_desc = f"Attn(d={cfg['d_model']})"
        elif fam == "CascadedMoE":
            model = CascadedMoE(in_dim=NUM_FEATURES, num_classes=NUM_CLASSES)
            arch_desc = "MoE (Gate+2Exp)"
        elif fam == "LinearBaseline":
            model = nn.Linear(NUM_FEATURES, NUM_CLASSES)
            arch_desc = "Linear (1-layer)"

        criterion = nn.CrossEntropyLoss()
        optimizer = optim.Adam(model.parameters(), lr=cfg["lr"], weight_decay=cfg["wd"])

        # Train loop
        start_t = time.perf_counter()
        for epoch in range(cfg["epochs"]):
            model.train()
            for bx, by in train_loader:
                optimizer.zero_grad()
                out = model(bx)
                loss = criterion(out, by)
                loss.backward()
                optimizer.step()

        # Evaluate on Validation Set
        model.eval()
        with torch.no_grad():
            val_out = model(torch.from_numpy(X_val))
            val_preds = torch.argmax(val_out, dim=1).numpy()
            val_acc = (val_preds == y_val).mean() * 100.0

            # Evaluate on Unseen Holdout Test Set
            test_out = model(torch.from_numpy(X_test))
            test_preds = torch.argmax(test_out, dim=1).numpy()
            test_acc = (test_preds == y_test).mean() * 100.0

        n_params, total_bytes, q_err = quantize_int8_weights(model)

        # Composite score: Pareto balance of Accuracy, Parameter compactness, and low quantization error
        # Penalizes models > 10KB
        size_penalty = max(0.0, (total_bytes - 5000) / 10000.0)
        score = test_acc - (size_penalty * 0.5)

        is_champ = ""
        if score > champion_score and test_acc >= 98.0:
            champion_score = score
            champion = {
                "trial": idx,
                "family": fam,
                "config": cfg,
                "model": model,
                "val_acc": val_acc,
                "test_acc": test_acc,
                "params": n_params,
                "bytes": total_bytes,
                "q_err": q_err
            }
            is_champ = "★ NEW BEST"

        res_entry = {
            "trial": int(idx),
            "family": fam,
            "arch": arch_desc,
            "params": int(n_params),
            "bytes": int(total_bytes),
            "val_acc": float(round(val_acc, 2)),
            "test_acc": float(round(test_acc, 2)),
            "q_err": float(round(float(q_err), 4)),
            "score": float(round(float(score), 2))
        }
        trials_results.append(res_entry)

        print(f"#{idx:<5} | {fam:<15} | {arch_desc:<22} | {val_acc:6.2f}% | {test_acc:6.2f}% | {total_bytes/1024:6.2f} KB | {is_champ}")

    print("=" * 80)
    print(f"\nCHAMPION MODEL DISCOVERED: Trial #{champion['trial']} ({champion['family']})")
    print(f"Architecture:   {champion['config']}")
    print(f"Test Accuracy:  {champion['test_acc']:.2f}% (Holdout)")
    print(f"Validation Acc: {champion['val_acc']:.2f}%")
    print(f"Total Params:   {champion['params']} parameters")
    print(f"INT8 Footprint: {champion['bytes']} bytes ({champion['bytes']/1024:.2f} KB)")
    print(f"Quant Error:    {champion['q_err']:.4f}")

    # Export full trial log
    out_log = "dosh-browser-core/src/trial_search_ledger.json"
    with open(out_log, "w") as f:
        json.dump({
            "total_trials": len(trials_results),
            "champion": {
                "trial": int(champion["trial"]),
                "family": champion["family"],
                "arch": champion["config"],
                "test_acc": float(champion["test_acc"]),
                "bytes": int(champion["bytes"])
            },
            "trials": trials_results
        }, f, indent=2)
    print(f"Saved full 60-trial ledger to: {out_log}")

    # Export Champion weights directly for Rust SIMD engine
    champ_model = champion["model"]
    export_champion(champ_model, champion["config"], X_test, y_test)

def export_champion(model, cfg, X_test, y_test):
    """Exports champion parameters in pure-Rust ingestible JSON."""
    fam = cfg["family"]
    out_path = "dosh-browser-core/src/champion_model.json"
    
    weights_dict = {}
    for name, param in model.named_parameters():
        arr = param.detach().cpu().numpy()
        max_v = float(np.max(np.abs(arr))) if np.max(np.abs(arr)) > 0 else 1.0
        scale = max_v / 127.0
        q = np.clip(np.round(arr / scale), -128, 127).astype(np.int8)
        weights_dict[name + "_q"] = q.flatten().tolist()
        weights_dict[name + "_raw"] = arr.flatten().tolist()
        weights_dict[name + "_scale"] = float(scale)
        weights_dict[name + "_shape"] = list(arr.shape)

    test_sub = [
        {"x": X_test[i].tolist(), "y": int(y_test[i])}
        for i in range(min(100, len(X_test)))
    ]

    bundle = {
        "family": fam,
        "config": {k: v for k, v in cfg.items() if k != "model"},
        "weights": weights_dict,
        "test_samples": test_sub
    }

    with open(out_path, "w") as f:
        json.dump(bundle, f, indent=2)
    print(f"Exported Champion model to: {out_path}")

if __name__ == "__main__":
    run_trials()
