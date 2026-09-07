"""A baseline skill vocabulary.

Used to detect what a JD asks for beyond what you already list, so that
`missing_skills` is a real gap list and not just "skills absent from my CV".
Extend it freely - anything you add here becomes detectable in the heatmap.
"""

from __future__ import annotations

COMMON_SKILLS: list[str] = [
    # languages
    "Python", "Java", "JavaScript", "TypeScript", "C", "C++", "C#", "Go", "Rust",
    "Ruby", "PHP", "Kotlin", "Swift", "Scala", "R", "MATLAB", "Perl", "Bash",
    "SQL", "PL/SQL", "T-SQL", "VBA", "Dart", "Objective-C", "Solidity",
    # web / frontend
    "HTML", "CSS", "SASS", "Tailwind", "Bootstrap", "React", "Next.js", "Angular",
    "Vue", "Svelte", "Redux", "jQuery", "Webpack", "Vite", "Three.js",
    # backend / frameworks
    "Node.js", "Express", "Django", "Flask", "FastAPI", "Spring", "Spring Boot",
    "Laravel", "Rails", "ASP.NET", ".NET", "GraphQL", "REST API", "gRPC",
    "Microservices", "Celery", "RabbitMQ", "Kafka",
    # data
    "Pandas", "NumPy", "SciPy", "Scikit-learn", "PyTorch", "TensorFlow", "Keras",
    "Spark", "PySpark", "Hadoop", "Hive", "Airflow", "dbt", "Snowflake",
    "BigQuery", "Redshift", "Databricks", "ETL", "Data Warehouse", "Tableau",
    "Power BI", "Looker", "Excel", "Statistics", "A/B Testing",
    # ai / ml
    "Machine Learning", "Deep Learning", "NLP", "Computer Vision", "LLM",
    "Generative AI", "Prompt Engineering", "LangChain", "RAG", "Hugging Face",
    "MLOps", "Feature Engineering", "Recommendation Systems", "OpenCV",
    # databases
    "PostgreSQL", "MySQL", "MongoDB", "Redis", "Elasticsearch", "Cassandra",
    "DynamoDB", "Oracle", "SQLite", "Neo4j", "Firebase", "Supabase",
    # cloud / infra
    "AWS", "Azure", "GCP", "Docker", "Kubernetes", "Terraform", "Ansible",
    "Jenkins", "GitHub Actions", "GitLab CI", "CI/CD", "Linux", "Nginx",
    "Serverless", "Lambda", "CloudFormation", "Prometheus", "Grafana",
    "Datadog", "Helm", "OpenShift",
    # mobile
    "Android", "iOS", "React Native", "Flutter", "Xamarin",
    # practice / tooling
    "Git", "Agile", "Scrum", "Jira", "TDD", "Unit Testing", "Pytest", "Selenium",
    "Cypress", "Playwright", "Postman", "System Design", "Distributed Systems",
    "Design Patterns", "Data Structures", "Algorithms", "OOP", "Multithreading",
    "Security", "OAuth", "JWT", "Web Sockets", "Caching", "Load Balancing",
    "Figma", "Jupyter", "Streamlit",
]

# Aliases collapse spelling variants onto one canonical name, so that
# "React.js", "ReactJS" and "React" are all the same skill.
DEFAULT_ALIASES: dict[str, list[str]] = {
    "React": ["React.js", "ReactJS", "React JS"],
    "Node.js": ["Node", "NodeJS", "Node JS"],
    "Next.js": ["NextJS", "Next JS"],
    "Vue": ["Vue.js", "VueJS"],
    "Angular": ["AngularJS", "Angular 2+"],
    "JavaScript": ["JS", "ECMAScript", "ES6"],
    "TypeScript": ["TS"],
    "PostgreSQL": ["Postgres", "psql"],
    "MongoDB": ["Mongo"],
    "Kubernetes": ["K8s", "k8"],
    "AWS": ["Amazon Web Services", "EC2", "S3"],
    "GCP": ["Google Cloud", "Google Cloud Platform"],
    "Azure": ["Microsoft Azure"],
    "Machine Learning": ["ML"],
    "Deep Learning": ["DL", "Neural Networks"],
    "NLP": ["Natural Language Processing"],
    "Computer Vision": ["CV", "Image Processing"],
    "LLM": ["Large Language Model", "Large Language Models", "GPT", "Foundation Models"],
    "Generative AI": ["GenAI", "Gen AI"],
    "RAG": ["Retrieval Augmented Generation", "Retrieval-Augmented Generation"],
    "CI/CD": ["CICD", "Continuous Integration", "Continuous Delivery"],
    "REST API": ["REST", "RESTful", "RESTful API", "RESTful APIs"],
    "Scikit-learn": ["sklearn", "scikit learn"],
    "PyTorch": ["Torch"],
    "TensorFlow": ["TF"],
    "Power BI": ["PowerBI"],
    "Data Structures": ["DSA", "Data Structures and Algorithms"],
    "Distributed Systems": ["Distributed Computing", "Large Scale Systems"],
    "Unit Testing": ["Unit Tests"],
    "GitHub Actions": ["GH Actions"],
    "Spring Boot": ["SpringBoot"],
    ".NET": ["dotnet", "DotNet", ".NET Core"],
}
