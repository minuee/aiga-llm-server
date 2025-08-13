const today = new Date().toISOString().slice(0, 10);

module.exports = {
    apps: [
      {
        name: "aiga-llm-renual",
        script: "./start_pm2.sh",
        interpreter: "bash",
        out_file: `./logs/aiga_llm_server/out-${today}.log`,
        error_file: `./logs/aiga_llm_server/err-${today}.log`,
        merge_logs: true,
        // 로그 파일의 타임스탬프 비활성화 (하나의 파일에 계속 쌓이도록)
        log_date_format: "", 
      },
    ],
  };