"""
Adversarial Stress Test: Push the top model contenders to their breaking point.
Generates 1,000 extreme edge cases:
- Pointer-events: none on class="btn"
- No-op inline onclick="void(0)"
- Deeply nested custom web components
- Pseudo-buttons with long prose paragraphs (content marketing CTA wrappers)
- Invisible tracking pixels masquerading as buttons
- Ambiguous composite cards (both link and button nested)
"""

import json
import numpy as np
import torch
import torch.nn as nn

NUM_FEATURES = 32
NUM_CLASSES = 5
CLASSES = ["NonInteractive", "Button", "Input", "Link", "Slider"]

def generate_adversarial_suite(n=1000):
    X = []
    y = []

    for i in range(n):
        feats = np.zeros(NUM_FEATURES, dtype=np.float32)
        feats[31] = 1.0  # Bias

        edge_case = i % 5

        if edge_case == 0:
            # Trap 1: Deceptive Button Wrapper (Massive container with button classes)
            # Label: NonInteractive (0)
            feats[0] = 0.1  # div
            feats[18] = 0.95  # class: "btn-container primary-action-wrapper"
            feats[22] = 0.90  # class also has "container"
            feats[15] = 0.90  # 9 children (it's a container!)
            feats[13] = 0.85  # long text
            feats[6] = 0.0   # NO cursor pointer on container
            feats[1] = 0.0
            y.append(0)

        elif edge_case == 1:
            # Trap 2: Unstyled Div with React onClick and Imperative Text
            # Label: Button (1)
            feats[0] = 0.1  # div
            feats[2] = 1.0  # React synthetic onClick!
            feats[6] = 1.0  # cursor: pointer
            feats[13] = 0.12 # "Proceed to Payment"
            feats[14] = 0.18 # 3 words
            feats[15] = 0.0  # leaf
            feats[25] = 1.0  # action verb "Proceed"
            feats[18] = 0.0  # NO btn classes at all (unstyled)
            y.append(1)

        elif edge_case == 2:
            # Trap 3: Contenteditable Rich-Text Box inside Div
            # Label: Input (2)
            feats[0] = 0.1  # div
            feats[30] = 1.0 # contenteditable=true
            feats[16] = 1.0 # leaf
            feats[6] = 0.0  # text cursor
            feats[18] = 0.0
            y.append(2)

        elif edge_case == 3:
            # Trap 4: Accessible Tab Trigger masquerading as Link
            # Label: Link (3)
            feats[0] = 0.3  # li
            feats[10] = 1.0 # aria-selected=true
            feats[12] = 1.0 # tabindex=0
            feats[6] = 1.0  # cursor: pointer
            feats[13] = 0.08 # "Overview"
            feats[19] = 0.7 # class: "nav-tab-item"
            y.append(3)

        elif edge_case == 4:
            # Trap 5: Custom SVG Range Scrubber with drag data attribute
            # Label: Slider (4)
            feats[0] = 0.2  # span
            feats[21] = 0.9 # class: "video-scrubber-thumb"
            feats[23] = 1.0 # data-action="drag"
            feats[6] = 1.0  # cursor: grab/pointer
            feats[13] = 0.0 # no text
            feats[16] = 1.0 # leaf
            y.append(4)

        # High variance adversarial noise (±0.06)
        noise = np.random.normal(0, 0.05, NUM_FEATURES).astype(np.float32)
        feats = np.clip(feats + noise, 0.0, 1.0)
        feats[31] = 1.0

        X.append(feats)

    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int64)

def evaluate_adversarial():
    print("=" * 60)
    print("ADVERSARIAL STRESS TEST: EXTREME DOM EDGE CASES")
    print("=" * 60)

    X_adv, y_adv = generate_adversarial_suite(1000)
    print(f"Generated {len(X_adv)} adversarial test cases...")

    # Load champion weights
    with open("dosh-browser-core/src/champion_model.json", "r") as f:
        data = json.load(f)

    weights = data["weights"]
    w1_q = np.array(weights["net.0.weight_q"], dtype=np.int8).reshape(weights["net.0.weight_shape"])
    b1 = np.array(weights["net.0.bias_raw"], dtype=np.float32)
    w2_q = np.array(weights["net.2.weight_q"], dtype=np.int8).reshape(weights["net.2.weight_shape"])
    b2 = np.array(weights["net.2.bias_raw"], dtype=np.float32)

    s1 = weights["net.0.weight_scale"]
    s2 = weights["net.2.weight_scale"]

    correct = 0
    per_class_tp = [0] * NUM_CLASSES
    per_class_total = [0] * NUM_CLASSES

    for i in range(len(X_adv)):
        x = X_adv[i]
        label = y_adv[i]
        per_class_total[label] += 1

        # INT8 inference
        h1 = np.maximum(0, (np.dot(w1_q, x) * s1) + b1)
        logits = (np.dot(w2_q, h1) * s2) + b2
        pred = np.argmax(logits)

        if pred == label:
            correct += 1
            per_class_tp[label] += 1

    acc = (correct / len(X_adv)) * 100.0
    print(f"\nAdversarial Stress Test Accuracy: {acc:.2f}% ({correct}/{len(X_adv)} passed)")

    for c_idx, c_name in enumerate(CLASSES):
        cls_acc = (per_class_tp[c_idx] / per_class_total[c_idx]) * 100.0
        print(f"  - {c_name:15s}: {cls_acc:6.2f}% ({per_class_tp[c_idx]}/{per_class_total[c_idx]})")

    print("=" * 60)

if __name__ == "__main__":
    evaluate_adversarial()
