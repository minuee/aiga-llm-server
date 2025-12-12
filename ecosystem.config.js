const today = new Date().toISOString().slice(0, 10);

module.exports = {
  apps: [
    {
      name: "aiga-llm-renual",
      script: "./start_pm2.sh",
      interpreter: "bash",
      // pm2가 콘솔 출력을 이 파일들로 리디렉션합니다.
      output: `./logs/aiga_llm_server/app-${today}.log`,
      error: `./logs/aiga_llm_server/error-${today}.log`,
      // 에러 로그와 일반 로그를 한 파일(output)에 합칩니다.
      merge_logs: true,
      // pm2 로그에 타임스탬프를 추가합니다.
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      // 환경 변수 추가
      env: {
        "NODE_ENV": "production",
        "LOG_HANDLER_TYPE": "pm2"
      }
    },
  ],
};
