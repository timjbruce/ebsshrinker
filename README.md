# ebsshrinker
usage > python storageshrink.py


## Requirements:
1) must be run on the instance to be updated
2) boto3 must be installed on the system (pip install boto3)
3) user must have sudo access to rm, mkdir, rsync, mount, umount, mkfs, file
4) user must have rights to create a file in the directory this is run from
5) instance must have a role with the policy in rolepolicy.json

### local.py
This file is meant to hold all the variables that are needed to resize EBS storage
on an ec2 instance.  The variables are defined as follows:
json_simple:
  instanceid - the InstanceId of the host to run the process on
  devices - an array of devices to adjust
    {blockdevice - the current device that is attached to the instance, using
        the console defined versions, not the symlink versions,
     data_dir - the mount point for the blockdevice filesystem,
     newsize - the new size of the ebs device, in gigabytes,
     newdevice - an unused device name on the instance to use for the new volume,
        again following the console defined versions
     temp_dir - temporary mount point for newdevice while copying data.
    }

region: the AWS region that the instance resides in

The process will perform a number of checks to make sure that the process could
complete, assuming no permission errors.
