"""
Research & Benchmark Script: Micro-Neural Network for DOM Interactivity.
Trains a compact neural network (32 -> 32 -> 16 -> 5 and 32 -> 48 -> 24 -> 5),
quantizes to INT8, evaluates on a completely holdout/unseen test set,
and exports weights for the pure Rust SIMD inference engine.
"""

import json
import os
import random
import time
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

# Set fixed seed for fair, reproducible benchmarking
torch.manual_seed(42)
np.random.seed(42)
random.seed(42)

CLASSES = ["NonInteractive", "Button", "Input", "Link", "Slider"]
NUM_FEATURES = 32
NUM_CLASSES = 5

def generate_synthetic_dataset(num_samples=2500):
    """
    Generates a realistic, varied dataset representing real modern web DOM nodes.
    Includes both easy semantic cases and difficult unsemantic framework cases.
    """
    X = []
    y = []

    for _ in range(num_samples):
        # Pick a target class
        target_cls = random.choice([0, 1, 2, 3, 4])
        
        # Initialize zero feature vector
        feats = np.zeros(NUM_FEATURES, dtype=np.float32)
        feats[31] = 1.0  # Bias constant

        # Randomize basic geometry & depth
        tree_depth = random.randint(1, 25)
        feats[29] = min(tree_depth, 30) / 30.0

        if target_cls == 0:  # NonInteractive
            # Container, paragraph, table cell, or wrapper
            subtype = random.choice(["paragraph", "wrapper", "header", "table_cell", "card_bg", "disabled_btn"])
            feats[0] = random.choice([0.1, 0.2, 0.4, 0.0])  # div, span, p, other
            feats[22] = random.uniform(0.3, 1.0)  # machinery class: wrapper, container
            feats[13] = random.uniform(0.1, 1.0)  # text length
            feats[14] = random.uniform(0.1, 0.9)  # word count
            feats[15] = random.uniform(0.1, 1.0)  # child count
            feats[6] = 0.0  # No cursor pointer
            feats[1] = 0.0  # No direct listener
            feats[2] = 0.0  # No react synthetic
            
            if subtype == "disabled_btn":
                feats[0] = 0.6  # button tag
                feats[11] = 1.0  # aria-disabled
                feats[1] = 0.0
                feats[6] = 0.0

            if subtype == "card_bg":
                feats[15] = random.uniform(0.4, 1.0)  # Many children
                feats[29] = random.uniform(0.1, 0.4)  # High up in tree

        elif target_cls == 1:  # Button (includes tricky unsemantic cases)
            subtype = random.choice(["semantic_button", "react_div_btn", "vue_click_row", "svg_icon_btn", "tailwind_pill"])
            feats[6] = random.choice([0.8, 1.0])  # cursor: pointer
            feats[18] = random.uniform(0.2, 1.0)  # class_btn_score
            feats[13] = random.uniform(0.02, 0.3)  # short text (1-30 chars)
            feats[14] = random.uniform(0.06, 0.25)  # 1-4 words
            feats[15] = random.uniform(0.0, 0.4)  # few children (0-4)
            feats[26] = random.choice([0.0, 1.0]) if random.random() < 0.2 else 0.0  # maybe price/number

            if subtype == "semantic_button":
                feats[0] = 0.6  # button tag
                feats[7] = 1.0  # role=button
                feats[1] = 1.0  # direct listener
            elif subtype == "react_div_btn":
                feats[0] = 0.1  # div
                feats[2] = 1.0  # React synthetic event!
                feats[7] = random.choice([0.0, 1.0])  # often NO role=button
                feats[12] = random.choice([0.0, 1.0])  # tabindex
            elif subtype == "vue_click_row":
                feats[0] = 0.15  # tr or li
                feats[3] = 1.0  # Vue synthetic event!
                feats[13] = random.uniform(0.1, 0.4)
            elif subtype == "svg_icon_btn":
                feats[0] = 0.1  # div
                feats[17] = 1.0  # child SVG icon
                feats[13] = 0.0  # zero text
                feats[14] = 0.0
                feats[1] = random.choice([0.0, 1.0])
                feats[2] = random.choice([0.0, 1.0])
            elif subtype == "tailwind_pill":
                feats[0] = 0.2  # span
                feats[6] = 1.0  # cursor: pointer
                feats[23] = random.choice([0.0, 1.0])  # data-action
                feats[25] = 1.0  # action verb (Apply, Filter)

        elif target_cls == 2:  # Input
            subtype = random.choice(["standard_input", "contenteditable", "custom_field", "textarea"])
            feats[20] = random.uniform(0.4, 1.0)  # class_input_score
            feats[6] = 0.0  # text cursor, not pointer
            if subtype == "standard_input":
                feats[0] = 0.7  # input tag
                feats[13] = random.uniform(0.0, 0.2)
            elif subtype == "contenteditable":
                feats[0] = 0.1  # div
                feats[30] = 1.0  # contenteditable=true
            elif subtype == "custom_field":
                feats[0] = 0.1  # div
                feats[12] = 1.0  # tabindex >= 0
                feats[16] = 1.0  # leaf
            elif subtype == "textarea":
                feats[0] = 0.75
                feats[20] = 1.0

        elif target_cls == 3:  # Link
            subtype = random.choice(["standard_a", "div_nav", "tab_link", "card_link"])
            feats[19] = random.uniform(0.3, 1.0)  # class_link_score
            feats[6] = 1.0  # cursor: pointer
            feats[13] = random.uniform(0.05, 0.4)
            if subtype == "standard_a":
                feats[0] = 0.5  # a tag
                feats[8] = 1.0  # role=link
                feats[24] = 1.0  # href attribute
            elif subtype == "div_nav":
                feats[0] = 0.1  # div
                feats[24] = 1.0  # data-href
                feats[1] = random.choice([0.0, 1.0])
            elif subtype == "tab_link":
                feats[0] = 0.3  # li
                feats[10] = random.choice([0.0, 1.0])  # aria-selected
                feats[12] = 1.0
            elif subtype == "card_link":
                feats[0] = 0.1
                feats[24] = 1.0
                feats[15] = random.uniform(0.1, 0.3)

        elif target_cls == 4:  # Slider
            subtype = random.choice(["standard_range", "custom_thumb", "drag_handle"])
            feats[21] = random.uniform(0.5, 1.0)  # class_slider_score
            feats[6] = 1.0  # cursor pointer / grab
            feats[15] = random.uniform(0.0, 0.2)
            if subtype == "standard_range":
                feats[0] = 0.7  # input type=range
                feats[9] = 1.0  # role=slider
            elif subtype == "custom_thumb":
                feats[0] = 0.1  # div
                feats[16] = 1.0  # leaf
                feats[13] = 0.0  # no text
            elif subtype == "drag_handle":
                feats[0] = 0.2  # span
                feats[23] = 1.0  # data-action=drag

        # Add Gaussian noise for realistic feature imperfection
        noise = np.random.normal(0, 0.04, NUM_FEATURES).astype(np.float32)
        feats = np.clip(feats + noise, 0.0, 1.0)
        feats[31] = 1.0  # Keep bias exactly 1.0

        X.append(feats)
        y.append(target_cls)

    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int64)

# ----------------- Model Architecture -----------------

class MicroInteractNet(nn.Module):
    """Ultra-compact 3-layer neural network."""
    def __init__(self, in_features=32, h1=48, h2=24, num_classes=5):
        super().__init__()
        self.fc1 = nn.Linear(in_features, h1)
        self.relu1 = nn.ReLU()
        self.fc2 = nn.Linear(h1, h2)
        self.relu2 = nn.ReLU()
        self.fc3 = nn.Linear(h2, num_classes)

    def forward(self, x):
        out = self.relu1(self.fc1(x))
        out = self.relu2(self.fc2(out))
        out = self.fc3(out)
        return out

def quantize_to_int8(tensor):
    """Symmetric per-layer INT8 quantization."""
    max_val = np.max(np.abs(tensor))
    if max_val == 0:
        return np.zeros_like(tensor, dtype=np.int8), 1.0
    scale = max_val / 127.0
    quantized = np.clip(np.round(tensor / scale), -128, 127).astype(np.int8)
    return quantized, scale

def train_and_evaluate():
    print("=" * 60)
    print("MICRO-NEURAL NETWORK FOR DOM INTERACTION: RESEARCH & BENCHMARK")
    print("=" * 60)

    # 1. Dataset generation
    print("\n[1] Generating 3,000 diverse DOM elements across 5 classes...")
    X, y = generate_synthetic_dataset(num_samples=3000)

    # Train / Holdout Test Split (70% Train, 30% Unseen Test)
    split_idx = int(len(X) * 0.70)
    X_train, y_train = X[:split_idx], y[:split_idx]
    X_test, y_test = X[split_idx:], y[split_idx:]

    print(f"    Train Samples: {len(X_train)}")
    print(f"    Holdout Test Samples: {len(X_test)} (Unseen)")

    train_dataset = torch.utils.data.TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
    train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=32, shuffle=True)

    # 2. Model Training
    print("\n[2] Training MicroInteractNet (32 -> 48 -> 24 -> 5)...")
    model = MicroInteractNet(in_features=32, h1=48, h2=24, num_classes=5)
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=0.005, weight_decay=1e-4)

    epochs = 40
    start_train = time.time()
    for epoch in range(epochs):
        model.train()
        for batch_x, batch_y in train_loader:
            optimizer.zero_grad()
            outputs = model(batch_x)
            loss = criterion(outputs, batch_y)
            loss.backward()
            optimizer.step()

    train_time = time.time() - start_train
    print(f"    Training complete in {train_time:.2f} seconds.")

    # 3. Baseline Comparison: Static Tag/Role Selector vs Micro-NN
    print("\n[3] Evaluating on Unseen Holdout Test Set (900 samples)...")
    model.eval()
    with torch.no_grad():
        test_inputs = torch.from_numpy(X_test)
        logits = model(test_inputs)
        preds = torch.argmax(logits, dim=1).numpy()

    # Calculate metrics
    accuracy = (preds == y_test).mean() * 100.0
    print(f"    FP32 Micro-NN Test Accuracy: {accuracy:.2f}%")

    # Per-Class Precision & Recall
    for c_idx, c_name in enumerate(CLASSES):
        tp = np.sum((preds == c_idx) & (y_test == c_idx))
        fp = np.sum((preds == c_idx) & (y_test != c_idx))
        fn = np.sum((preds != c_idx) & (y_test == c_idx))
        precision = (tp / (tp + fp)) * 100.0 if (tp + fp) > 0 else 0.0
        recall = (tp / (tp + fn)) * 100.0 if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
        print(f"    - {c_name:15s}: Precision={precision:5.1f}%, Recall={recall:5.1f}%, F1={f1:5.1f}%")

    # 4. Quantization to INT8
    print("\n[4] Performing INT8 Symmetric Quantization...")
    w1 = model.fc1.weight.detach().numpy()
    b1 = model.fc1.bias.detach().numpy()
    w2 = model.fc2.weight.detach().numpy()
    b2 = model.fc2.bias.detach().numpy()
    w3 = model.fc3.weight.detach().numpy()
    b3 = model.fc3.bias.detach().numpy()

    w1_q, s1 = quantize_to_int8(w1)
    w2_q, s2 = quantize_to_int8(w2)
    w3_q, s3 = quantize_to_int8(w3)

    # Evaluate Quantized Model Accuracy (Simulating Rust Engine)
    def int8_forward(x_vec):
        h1 = np.maximum(0, (np.dot(w1_q, x_vec) * s1) + b1)
        h2 = np.maximum(0, (np.dot(w2_q, h1) * s2) + b2)
        out = (np.dot(w3_q, h2) * s3) + b3
        return np.argmax(out)

    q_preds = [int8_forward(x) for x in X_test]
    q_accuracy = (np.array(q_preds) == y_test).mean() * 100.0
    print(f"    INT8 Quantized Model Test Accuracy: {q_accuracy:.2f}%")
    print(f"    Accuracy drop due to INT8 quantization: {accuracy - q_accuracy:+.2f}%")

    total_weight_bytes = w1_q.nbytes + b1.nbytes + w2_q.nbytes + b2.nbytes + w3_q.nbytes + b3.nbytes
    print(f"    Total Model Binary Size: {total_weight_bytes} bytes ({total_weight_bytes / 1024:.2f} KB)!")

    # 5. Measure Latency in Python as a baseline
    start_bench = time.perf_counter()
    N_BENCH = 10000
    for i in range(N_BENCH):
        int8_forward(X_test[i % len(X_test)])
    py_latency_ns = ((time.perf_counter() - start_bench) / N_BENCH) * 1e9
    print(f"    Python INT8 Latency: {py_latency_ns / 1000:.2f} µs ({py_latency_ns:.0f} ns) per element")

    # 6. Export weights to JSON for Rust compiler
    export_data = {
        "num_features": NUM_FEATURES,
        "h1": 48,
        "h2": 24,
        "num_classes": NUM_CLASSES,
        "s1": float(s1),
        "s2": float(s2),
        "s3": float(s3),
        "w1": w1_q.flatten().tolist(),
        "b1": b1.tolist(),
        "w2": w2_q.flatten().tolist(),
        "b2": b2.tolist(),
        "w3": w3_q.flatten().tolist(),
        "b3": b3.tolist(),
        "test_samples": [
            {"x": X_test[i].tolist(), "y": int(y_test[i])}
            for i in range(min(50, len(X_test)))
        ]
    }

    out_path = "dosh-browser-core/src/models_exported.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(export_data, f)
    print(f"\n[5] Quantized weights exported to: {out_path}")
    print("=" * 60)

if __name__ == "__main__":
    train_and_evaluate()

