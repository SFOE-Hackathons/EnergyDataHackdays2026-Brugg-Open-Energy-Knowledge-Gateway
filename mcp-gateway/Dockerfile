# The AWS Lambda Python base image, which ships the Runtime Interface Client
# as its entrypoint. The gateway reaches this function through the Lambda
# Invoke API rather than over HTTP, so the container's job is to answer
# invocations, not to serve a port.
#
# An earlier revision built on python:3.12-slim and added the Lambda Web
# Adapter, so that one image could both serve MCP over HTTP locally and run
# behind a Lambda Function URL. That design is gone: AgentCore's outbound SigV4
# signer signs POST bodies as empty, so it cannot call an IAM-authorised
# Function URL at all (see lambda_handler.py). With the Function URL removed
# there is no second deployment left for one image to serve, and the adapter
# was only ever there to bridge to it.
#
# Serving MCP over HTTP for local work is unaffected and does not need this
# image -- `python dev_run.py` runs server.py directly on the host.
FROM public.ecr.aws/lambda/python:3.12

# LAMBDA_TASK_ROOT is where the runtime looks for the handler module.
WORKDIR ${LAMBDA_TASK_ROOT}

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -t "${LAMBDA_TASK_ROOT}"

# Listed explicitly rather than `COPY *.py` so dev_run.py and client_example.py
# stay out of the image. The cost is that a new module must be added here too,
# and forgetting produces an image that only fails at import time -- so if you
# add a module, add it to this line in the same commit.
COPY server.py config.py knowledge_base.py chart_parsing.py passages.py \
     public_source.py semantics.py tool_schema.py lambda_handler.py ./
COPY tools/ tools/

# Unbuffered stdout is what makes log lines appear in CloudWatch promptly
# rather than at container teardown.
ENV PYTHONUNBUFFERED=1

CMD ["lambda_handler.handler"]
