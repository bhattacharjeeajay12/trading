# ===============================
# model_train.py (TCN VERSION)
# ===============================

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
import pandas as pd
import numpy as np

from sklearn.metrics import classification_report, recall_score

from pytorch_tcn import TCN
from data_pipeline import prepare_data


# -------------------------------
# DEVICE
# -------------------------------
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print("Using device:", device)

if device.type == "cuda":
    print("GPU:", torch.cuda.get_device_name(0))


# -------------------------------
# LOAD DATA
# -------------------------------
df = pd.read_csv(r"D:\Study\Programs\trading\Experiments\TCNN\data\data.csv")

X_train, X_test, y_train, y_test, df, num_classes = prepare_data(
    df,
    use_sell_label=False,     # binary: BUY vs NOT BUY
    seq_len=120               # 🔥 longer context for TCN
)


# -------------------------------
# TORCH CONVERSION
# -------------------------------
X_train = torch.tensor(X_train, dtype=torch.float32).permute(0, 2, 1)
X_test  = torch.tensor(X_test, dtype=torch.float32).permute(0, 2, 1)

y_train = torch.tensor(y_train, dtype=torch.long)
y_test  = torch.tensor(y_test, dtype=torch.long)


# -------------------------------
# CLASS WEIGHTS (CRITICAL)
# -------------------------------
class_counts = np.bincount(y_train.numpy())
print("Class counts:", class_counts)

ratio = class_counts[0] / (class_counts[1] + 1e-6)
weights = torch.tensor([1.0, ratio], dtype=torch.float32).to(device)

print("Using weights:", weights)


# -------------------------------
# DATALOADER
# -------------------------------
train_loader = DataLoader(
    TensorDataset(X_train, y_train),
    batch_size=256,
    shuffle=False,
    pin_memory=True
)


# -------------------------------
# TCN MODEL
# -------------------------------
class Temporalc(nn.Module):
    def __init__(self, num_features, num_classes):
        super().__init__()

        self.tcn = TCN(
            num_inputs=num_features,
            num_channels=[32, 64, 64],
            kernel_size=3,
            dropout=0.2
        )

        self.fc = nn.Linear(64, num_classes)

    def forward(self, x):
        # x shape: (batch, features, seq_len)

        x = self.tcn(x)              # (batch, channels, seq_len)
        x = x[:, :, -1]              # last timestep

        return self.fc(x)


model = TemporalTCN(X_train.shape[1], num_classes).to(device)

criterion = nn.CrossEntropyLoss(weight=weights)
optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)


# -------------------------------
# TRAINING
# -------------------------------
EPOCHS = 100

for epoch in range(EPOCHS):
    model.train()
    total_loss = 0

    for xb, yb in train_loader:
        xb = xb.to(device, non_blocking=True)
        yb = yb.to(device, non_blocking=True)

        optimizer.zero_grad()

        out = model(xb)
        loss = criterion(out, yb)

        loss.backward()
        optimizer.step()

        total_loss += loss.item()

    print(f"Epoch {epoch+1}, Loss: {total_loss:.4f}")


# -------------------------------
# SAVE MODEL
# -------------------------------
torch.save(model.state_dict(), "model_tcn.pth")


# -------------------------------
# EVALUATION
# -------------------------------
model.eval()

X_test = X_test.to(device)
y_test = y_test.to(device)

with torch.no_grad():
    logits = model(X_test)

    # probabilities
    probs = torch.softmax(logits, dim=1)

    preds = torch.argmax(probs, dim=1)

accuracy = (preds == y_test).float().mean()
print("\nTest Accuracy:", accuracy.item())


# -------------------------------
# CLASSIFICATION REPORT
# -------------------------------
y_true = y_test.cpu().numpy()
y_pred = preds.cpu().numpy()

print("\nClassification Report:\n")
print(classification_report(y_true, y_pred))

buy_recall = recall_score(y_true, y_pred, pos_label=1)
print("BUY Recall:", buy_recall)


# -------------------------------
# PROBABILITY ANALYSIS
# -------------------------------
buy_probs = probs[:, 1].cpu().numpy()

df.loc[df['split'] == 'test', 'buy_prob'] = buy_probs

print("\nSample probabilities:")
print(df[['buy_prob']].dropna().head())


# -------------------------------
# OPTIONAL: THRESHOLD TESTING
# -------------------------------
print("\n--- Threshold Experiments ---")

for t in [0.5, 0.6, 0.7, 0.8, 0.9]:
    preds_t = (buy_probs > t).astype(int)

    print(f"\nThreshold: {t}")
    print(classification_report(y_true, preds_t))
