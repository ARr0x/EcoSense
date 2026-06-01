# EcoSense


ecosense/
├── .env.example
├── .env
├── .gitignore
├── README.md
│
├── iac/
│   ├── app.py
│   ├── stacks/
│   │   ├── __init__.py
│   │   ├── iot_core_stack.py
│   │   ├── compute_stack.py
│   │   ├── storage_stack.py
│   │   └── monitoring_stack.py
│   └── requirements.txt
│
├── lambdas/
│   ├── alert_processor.py     # Code Python (importé par compute_stack)
│   └── archiving_processor.py
│
├── simulator/
│   ├── simulator_http_sigv4.py
│   ├── requirements.txt       # boto3, requests, python-dotenv
│   └── failover_config.json   # endpoints IoT + régions
│
├── docs/
│   ├── architecture/
│   │   ├── *.drawio           # Schémas Draw.io
│   │   ├── *.png              # Exports PNG
│   │   └── *.mermaid          # Diagrammes
│   ├── runbooks/
│   │   ├── failover.md
│   │   ├── dlq-replay.md
│   │   └── region-loss.md
│   └── index.md
│
├── mkdocs.yml                # Config MkDocs Material
├── Makefile                  # Helpers deploy/test/destroy
└── cdk.json                  # Config CDK (context)
