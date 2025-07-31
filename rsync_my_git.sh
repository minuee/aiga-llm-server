rm -rf /Users/kormedi/Documents/WorkPlace/github/minuee-aiga-llm/*
rsync -rub --delete --exclude={'node_modules','logs','.git','.venv'} /Users/kormedi/Documents/WorkPlace/bitbucket/aiga-llm-server/* /Users/kormedi/Documents/WorkPlace/github/minuee-aiga-llm/
