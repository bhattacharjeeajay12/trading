# ===============================
# model_train.py (CUDA ENABLED)
# ===============================

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, TensorDataset
import pandas as pd

from data_pipeline import prepare_data

# -------------------------------
# DEVICE SETUP (GPU / CPU)
# -------------------------------
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print("Using device:", device)

if device.type == "cuda":
    print("GPU:", torch.cuda.get_device_name(0))



# -------------------------------
# LOAD DATA
# -------------------------------
df = pd.read_csv(r"D:\Study\Programs\trading\Experiments\TCNN\data\data.csv")

X_train, X_test, y_train, y_test, df = prepare_data(df)
# df.to_csv(r"D:\Study\Programs\trading\Experiments\TCNN\data\processed_data.csv")
# -------------------------------
# CONVERT TO TORCH
# -------------------------------
X_train = torch.tensor(X_train, dtype=torch.float32).permute(0, 2, 1)
X_test  = torch.tensor(X_test, dtype=torch.float32).permute(0, 2, 1)

y_train = torch.tensor(y_train, dtype=torch.long)
y_test  = torch.tensor(y_test, dtype=torch.long)

train_loader = DataLoader(
    TensorDataset(X_train, y_train),
    batch_size=256,
    shuffle=False,
    pin_memory=True  # helps GPU transfer
)


# -------------------------------
# MODEL
# -------------------------------
class TemporalCNN(nn.Module):
    def __init__(self, num_features, num_classes=3):
        super().__init__()

        self.conv1 = nn.Conv1d(num_features, 32, kernel_size=3, padding=1)
        self.bn1 = nn.BatchNorm1d(32)

        self.conv2 = nn.Conv1d(32, 64, kernel_size=3, padding=1)
        self.bn2 = nn.BatchNorm1d(64)

        self.conv3 = nn.Conv1d(64, 64, kernel_size=3, padding=1)
        self.bn3 = nn.BatchNorm1d(64)

        self.dropout = nn.Dropout(0.2)
        self.fc = nn.Linear(64, num_classes)

    def forward(self, x):
        x = F.relu(self.bn1(self.conv1(x)))
        x = F.relu(self.bn2(self.conv2(x)))
        x = F.relu(self.bn3(self.conv3(x)))

        x = x.mean(dim=2)  # global average pooling
        x = self.dropout(x)
        return self.fc(x)


model = TemporalCNN(num_features=X_train.shape[1]).to(device)

criterion = nn.CrossEntropyLoss()
optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)


# -------------------------------
# TRAINING LOOP
# -------------------------------
EPOCHS = 10

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
# EVALUATION
# -------------------------------
model.eval()

X_test = X_test.to(device)
y_test = y_test.to(device)

with torch.no_grad():
    logits = model(X_test)
    preds = torch.argmax(logits, dim=1)

accuracy = (preds == y_test).float().mean()
print("Test Accuracy:", accuracy.item())
