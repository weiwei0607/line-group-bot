# gunicorn 會自動讀取工作目錄的 gunicorn.conf.py（Render 的啟動指令不用改）。
# 仍是 1 個 process（排程鎖／SQLite 狀態假設單 process），只加 threads 讓慢請求不擋住其他 webhook。
# 命令列參數（--workers 1 --bind ...）優先於此檔；threads>1 時 gunicorn 自動改用 gthread worker。
threads = 4
timeout = 60
