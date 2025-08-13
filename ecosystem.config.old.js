module.exports = {
  apps: [
    {
      name: "aiga-llm-renual",
      script: "start_bk20250813.sh",
      args: "app.main:app --host 0.0.0.0 --port 8080 --log-level info",
      interpreter: "/bin/bash",
      exec_mode: "fork",
      env: {
        VIRTUAL_ENV: "./.venv",
        PATH: "./.venv/bin:$PATH",
      },
      error_file: "./logs/aiga_llm_server/err.log",
      out_file: "./logs/aiga_llm_server/out.log",
      merge_logs: true,
    },
  ],
};
