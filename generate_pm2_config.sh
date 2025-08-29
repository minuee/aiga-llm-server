#!/bin/bash

# Get today's date in YYYY-MM-DD format
TODAY=$(date +%Y-%m-%d)

# Define the content of ecosystem.config.js with the current date
# Make sure the path to ecosystem.config.js is correct for your setup
CONFIG_CONTENT="""const today = new Date().toISOString().slice(0, 10);

module.exports = {
    apps: [
      {
        name: "aiga-llm-renual",
        script: "./start_pm2.sh",
        interpreter: "bash",
        out_file: `./logs/aiga_llm_server/out-${TODAY}.log`,
        error_file: `./logs/aiga_llm_server/err-${TODAY}.log`,
        merge_logs: true,
        log_date_format: "",
      },
    ],
  };
"""
    # Write the content to the ecosystem.config.js file
    # Ensure this path is absolute and correct for your project
    echo "$CONFIG_CONTENT" > /Users/kormedi/Documents/WorkPlace/bitbucket/aiga-llm-server/ecosystem.config.js

    # Reload PM2 to pick up the new configuration
    # This command should be run after the config file is updated
    pm2 reload aiga-llm-renual