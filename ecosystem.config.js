/**
 * PM2 ecosystem config for Habib Distribution OS
 * VPS: habib@204.168.188.203 (Hetzner CX22, Ubuntu 24.04)
 *
 * Usage:
 *   pm2 start ecosystem.config.js
 *   pm2 save
 *   pm2 startup
 */

module.exports = {
  apps: [
    {
      name: "habib-scheduler",
      script: ".venv/bin/python",
      args: "-m src.jobs.scheduler",
      cwd: "/home/habib/habib-os",
      interpreter: "none",
      watch: false,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      env: {
        PYTHONPATH: "/home/habib/habib-os",
        ENVIRONMENT: "production",
      },
      error_file: "/home/habib/logs/scheduler-err.log",
      out_file: "/home/habib/logs/scheduler-out.log",
      log_date_format: "YYYY-MM-DD HH:mm:ss",
    },
    {
      name: "habib-telegram-bot",
      script: ".venv/bin/python",
      args: "-m src.telegram.bot",
      cwd: "/home/habib/habib-os",
      interpreter: "none",
      watch: false,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      env: {
        PYTHONPATH: "/home/habib/habib-os",
        ENVIRONMENT: "production",
      },
      error_file: "/home/habib/logs/bot-err.log",
      out_file: "/home/habib/logs/bot-out.log",
      log_date_format: "YYYY-MM-DD HH:mm:ss",
    },
  ],
};
