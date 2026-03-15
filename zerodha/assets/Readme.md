# Assets Folder

The **`assets`** folder is used for storing all log-related assets.

> **Note:**
> This folder will always remain **empty in the Git repository**.
> However, on the **server**, it contains the generated log assets described below.

## Contents Generated on the Server

The following files are stored in this folder on the server:

1. **Raw Log File**

   * Original log data generated during runtime.

2. **Index Tick Data**

   * Stored in **Excel format**.
   * Generated from the raw log file **after market hours**.

3. **ITM Options Tick Data**

   * Tick data for **In-The-Money (ITM) options instruments**.
   * Stored in **Excel format**.
   * Generated from the raw log file **after market hours**.

4. **Instrument Candle Data**

   * **1-minute candle data**.
   * Stored in **Excel format**.
   * Generated from the raw log file **after market hours**.
