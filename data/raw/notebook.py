# %%
import pandas as pd
import glob
import time
import duckdb

#%%
conn = duckdb.connect("test.db")

#%%
cur_time = time.time()
df = pd.concat([pd.read_parquet(f) for f in glob.glob("../raw/yellow/*.parquet")])
print (f"Time taken to read Parquet files: {time.time() - cur_time} ")
print (df.head(10))

#%%
cur_time = time.time()
df = conn.execute("""
    SELECT *
    FROM read_parquet("../raw/yellow/*.parquet")
""").df()
print (f"Time taken to read Parquet files: {time.time() - cur_time} ")
print (df.head(10))


# %%
df = conn.execute("""
    SELECT *
    FROM read_parquet("../raw/yellow/*.parquet")
""").df()
conn.register("df_view", df)
conn.execute("DESCRIBE df_view").df()
# %%
conn.execute("SELECT COUNT(*) FROM df_view").df()
# %%
