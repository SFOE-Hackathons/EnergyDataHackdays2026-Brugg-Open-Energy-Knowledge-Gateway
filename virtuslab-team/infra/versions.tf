terraform {
  # 1.11 is the floor: S3 native state locking (use_lockfile) went GA there and
  # the DynamoDB locking arguments were deprecated in the same release.
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.63"
    }
    # Reading the Bedrock Knowledge Base. hashicorp/aws ships knowledge_base.go
    # and data_source.go as *resources* only - it has no KB data source - and we
    # must never manage BFE's KB. hashicorp/awscc was the obvious alternative,
    # but it reads through Cloud Control, which BFE's SCP denies account-wide
    # (see data.tf). So the read shells out to the AWS CLI instead.
    external = {
      source  = "hashicorp/external"
      version = "~> 2.3"
    }
  }

  # Partial configuration. The profile is supplied by the Makefile via
  # -backend-config so that state always lives in the VirtusLab sandbox account
  # even when the *provider* is pointed at BFE's account.
  # State lives in the BFE account, alongside what it manages. It used to sit
  # in the VirtusLab sandbox, which meant every session began with an SSO login
  # that expires in ~8h, and teammates holding only BFE credentials got 403 on
  # it. BFE access is static keys, so neither is true any more.
  #
  # kms_key_id is REQUIRED, not a hardening nicety: `encrypt = true` alone
  # sends an explicit AES256 header, and the BFE org resource control policy
  # p-02xqecd89i denies any PutObject that is not KMS-encrypted. Without it
  # every state write - and every .tflock - fails with AccessDenied.
  backend "s3" {
    bucket       = "vl-bfe-kg-tfstate-542202863496"
    key          = "bfe-knowledge-gateway/terraform.tfstate"
    region       = "eu-central-1"
    encrypt      = true
    kms_key_id   = "alias/aws/s3"
    use_lockfile = true
  }
}
