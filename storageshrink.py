#process: EBSshrinker
#code provided as sample, no warranty applies

import boto3
import sys
import os
import subprocess
import time
import local
import logging
import datetime
import requests

logging.basicConfig(filename='storageshrinker.log', level=logging.DEBUG, filemode='w')
logging.info('============================Start: {}'.format(datetime.datetime.now()))

ec2 = boto3.client('ec2', region_name=local.region)
myinstanceid = requests.get('http://169.254.169.254/latest/meta-data/instance-id').text

#Doing a large set of validations up front.  If any of these fail, the process will exit
#Checks are, in order:
#   a) the current instance is the same as in local.py.  This is due to the need to run
#       unix commands for the instance
#   b) Not using / as the temp or data dir.  This can have disastrous effects on the instance
#       as the process will rm the temp directory at the end of the steps.
#   c) Using a temp_dir that already exists on the host.  This will be where the new volume
#       is initially mounted to for the rsync of files from the old volume
#   d) Identifying a data_dir that does not exist.  No where to copy files from!
#   e) Validating the instance is
#        1) running
#        2) has a tag for "in_service" with a value of "No"
#        3) that the operation is not requesting to resize the root filesystem
#        4) that the requested volume does not already exist on the instance
#        5) that the old volume exists on the instance


#Checks that cannot be done are
#   a) that the old volume is mounted to data_dir - this is due to devices being renamed from
#      the connection point to the instance.  This differs based on the OS and if there is hvm
#      or NVMe.  See https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/device_naming.html for
#      more details
#      As of such, this is a use at your own risk script.  Please make sure the settings in
#      local.py are what you want to execute!

#validate the current instance
logging.info('Checking for instance {}'.format(local.json_simple['instanceid']))
if not local.json_simple['instanceid']==myinstanceid:
    print('Not running on the proper instance!')
    logging.info('Attempted to run process for {} on {}'.format(local.json_simple['instanceid'], myinstanceid))
    exit()

#loop around all the devices in local.json_simple
for device in local.json_simple['devices']:
    #Validate that temp_dir does not yet exist and that data_dir does exist
    logging.info('Checking for {}'.format(device['temp_dir']))
    if device['temp_dir']=='/' or device['data_dir']:
        print('Cannot use / as temp_dir or data_dir!')
        logging.info('Attempted to use / as temp_dir or data_dir')
        continue
    if os.path.exists(device['temp_dir']):
        print('Cannot proceed - {} already exists.  Please update either your local.py file or remove the directory.'.format(device['temp_dir']))
        logging.info('Directory temp_dir:{} already exists.'.format(device['temp_dir']))
        continue
    if not os.path.exists(device['data_dir']):
        print('Cannot proceed - {} does not exist to copy data from.'.format(device['data_dir']))
        logging.info('Directory data_dir:{} does not exists.'.format(device['temp_dir']))
        continue
    logging.debug('Entering the try block.')

    try:
        logging.info('ec2.describe_instances, checking for {}.'.format(local.json_simple['instanceid']))
        #first things first - get the instance and storage
        instance_response = ec2.describe_instances(InstanceIds=[local.json_simple['instanceid']])
        for instance in instance_response['Reservations'][0]['Instances']:
            state = instance['State']['Name']
            logging.info('Found the instance {} and its state is {}'.format(local.json_simple['instanceid'], state))
            print('Got the instance {}.  It is {}.'.format(instance['InstanceId'], state))
            if not state == 'running':
                print('Instance must be running.  Please start the instance and try the script again.')
                logging.info('Instance {} state was {}.'.format(local.json_simple['instanceid'], state))
                continue
            #get the tag and make sure in_service = no
            logging.info('Checking tags on instance {}'.format(local.json_simple['instanceid']))
            instance_tags = instance['Tags']
            proceed=False
            for tag in instance_tags:
                if tag['Key'] == 'in_service':
                    if tag['Value'] == 'No':
                        logging.info('Tag {} found and value is {}.  We are good to go.'.format(tag['Key'], tag['Value']))
                        print('Instance marked for out of service.  Ready to proceed.')
                        proceed = True
                    else:
                        logging.info('Tag {} found and value is {}.  We are not good to go.'.format(tag['Key'], tag['Value']))
                        logging.info('===================End of run')
                        print('Instance not marked for out of service.  Not ready to proceed.')
                        exit()
            if not proceed:
                print('Tag for in_service not found.  Please update the tag and try again.')
                logging.info('Tag for out of service not found on instance {}.  We are not good to go.'.format(local.json_simple['instaceid']))
                logging.info('===================End of run')
                exit()
            #make sure the device is not the root device - cannot do this for root devices
            logging.info('Checking to make sure operation is not on {}.'.format(instance['RootDeviceName']))
            if instance['RootDeviceName'] == device['blockdevice']:
                print('Cannot perform the operation on the root device.')
                logging.info('Cannot perform the operation on {} as it is the root device of {}'.format(instance['RootDeviceName'], local.json_simple['instanceid']))
                continue
            #make sure the device you are trying to use is available on the instance
            for blockdevices in instance['BlockDeviceMappings']:
                if blockdevices['DeviceName'] == device['newdevice']:
                    #device is already in use.
                    print('Cannot perform the requested operation as there is a volume already attached to the instance using the requested device name.')
                    logging.info('{} is already attached to the instance.  Cannot perform the entire operation'.format(device['newdevice']))
                    continue
            #make sure the device is connected to the host!
            logging.info('Checking the EBS volumes for {}'.format(device['blockdevice']))
            print('Checking the connected EBS Volumes for {}'.format(device['blockdevice']))
            for blockdevices in instance['BlockDeviceMappings']:
                print('Checking for {}'.format(device['blockdevice']))
                devicefound = False
                if blockdevices['DeviceName'] == device['blockdevice']:
                    devicefound = True
                    #get a full description of the volume, as we will use the same settings
                    #from the old volume to create the new volume EXCEPT for size, which is deteremined
                    #from the json input file
                    ebs_response = ec2.describe_volumes(VolumeIds=[blockdevices['Ebs']['VolumeId']])
                    old_device = ebs_response['Volumes'][0]
                    device_id = blockdevices['Ebs']['VolumeId']
                    print('Found!  Storage for {} in AZ: {}'.format(device_id,old_device['AvailabilityZone']))
                    print('Creating snapshot of old disk and allocating storage for replacement')
                    logging.info('Found the device.  Storage for {} in AZ: {}'.format(device_id,old_device['AvailabilityZone']))
                    logging.info('Creating snapshot and allocating new storage.')
                    snapshot_response = ec2.create_snapshot(Description='Snapshot from EBS Shrinker of {} on {}'.format(blockdevices['Ebs']['VolumeId'], local.json_simple['instanceid']),
                        VolumeId=old_device['VolumeId'], DryRun=False)
                    snapshotid = snapshot_response['SnapshotId']
                    #create the tags for the snapshot
                    dt = datetime.datetime.now()
                    tag_response = ec2.create_tags(Resources=[snapshotid],
                                Tags=[
                                    {'Key': 'ProcessCreatedBy', 'Value': 'EBSShrink'},
                                    {'Key': 'ProcessCreatedTimestamp', 'Value': dt.strftime('%m/%d/%Y %H:%M:%S')},
                                    {'Key': 'OriginalHost', 'Value': local.json_simple['instanceid']},
                                    {'Key': 'VolumeId', 'Value': blockdevices['Ebs']['VolumeId']},
                                ]
                            )
                    #create the new volume with the same settings from the old, except for size!
                    #Note: iops is only supported for io1 volumes.
                    #Note: KmsKeyId is also not included in this example and can be added to both calls
                    #TODO:  It would be a good feature to allow a new device type to be defined from the local.simple_json construct
                    # instead of from the prior drive.  This method could have impacts on iops for a volume
                    if old_device['VolumeType'] == 'io1':
                        newvolume_response = ec2.create_volume(AvailabilityZone=old_device['AvailabilityZone'], Encrypted=old_device['Encrypted'],
                            Iops=old_device['Iops'], Size=device['newsize'], VolumeType=old_device['VolumeType'])
                            #TagSpecifications=[{'ResourceType':'volume', 'Tags':old_device['Tags']}])
                            #need to add the tags after calling create
                    else:
                        newvolume_response = ec2.create_volume(AvailabilityZone=old_device['AvailabilityZone'], Encrypted=old_device['Encrypted'],
                            Size=device['newsize'], VolumeType=old_device['VolumeType'])
                            #, TagSpecifications=[{'ResourceType':'volume', 'Tags':old_device['Tags']}])
                            #need to add tags after calling create

                    #wait here until snapshot is complete and new disk is available
                    in_progress = True
                    cur_status_snapshot = ''
                    cur_status_create = ''
                    #add in the old tags
                    tag_response = ec2.create_tags(Resources=[newvolume_response['VolumeId']],
                                Tags=old_device['Tags'])
                    #add in tags from the process
                    tag_response = ec2.create_tags(Resources=[newvolume_response['VolumeId']],
                                Tags=[
                                    {'Key': 'ProcessCreatedBy', 'Value': 'EBSShrink'},
                                    {'Key': 'ProcessCreatedTimestamp', 'Value': dt.strftime('%m/%d/%Y %H:%M:%S')},
                                    {'Key': 'HostAssigned', 'Value': local.json_simple['instanceid']},
                                    {'Key': 'ReplacesVolumeId', 'Value': blockdevices['Ebs']['VolumeId']},
                                ]
                            )
                    while in_progress:
                        #snapshots can take a while, depending on time.  Sleep before calling for a status
                        #additionally, this will check the new volume status
                        time.sleep(10)
                        if cur_status_snapshot != 'completed':
                            snap_response = ec2.describe_snapshots(SnapshotIds=[snapshotid])
                            cur_progress_snapshot = snap_response['Snapshots'][0]['Progress']
                            cur_status_snapshot = snap_response['Snapshots'][0]['State']
                        if cur_status_create != 'ok':
                            create_response = ec2.describe_volume_status(VolumeIds=[newvolume_response['VolumeId']])
                            cur_status_create = create_response['VolumeStatuses'][0]['VolumeStatus']['Status']
                        print('Snapshopt {}: {} complete - Create {}'.format(cur_status_snapshot, cur_progress_snapshot, cur_status_create))
                        if cur_status_snapshot == 'completed' and cur_status_create == 'ok':
                            in_progress = False
                    print('Snapshot complete and new storage is ready to be connected!')
                    logging.info('Snapshot is complete and new storage is ready to be connected.')

                    #attach new volume at the specified mount point
                    response = ec2.attach_volume(Device=device['newdevice'], VolumeId=newvolume_response['VolumeId'],InstanceId=local.json_simple['instanceid'])
                    attaching = True
                    while attaching:
                        time.sleep(10)
                        attach_response = ec2.describe_volume_status(VolumeIds=[newvolume_response['VolumeId']])
                        if attach_response['VolumeStatuses'][0]['VolumeStatus']['Status']=='ok':
                            attaching=False
                    print('New storage connected.  Setting up file system and copying files from {} to {}'.format(device['data_dir'], device['temp_dir']))
                    logging.info('New storage is connected to host {}'.format(local.json_simple['instanceid']))

                    #time for unix commands to make the disk usable
                    logging.info('Setting up disk for usage.')
                    p1 = subprocess.Popen('sudo file -s {}'.format(device['newdevice']), shell=True).wait()
                    p2 = subprocess.Popen('sudo mkfs -t ext4 {}'.format(device['newdevice']), shell=True).wait()
                    p3 = subprocess.Popen('sudo mkdir {}'.format(device['temp_dir']), shell=True).wait()
                    logging.info('Mounting disk to temporary directory.')
                    p4 = subprocess.Popen('sudo mount {} {}'.format(device['newdevice'],device['temp_dir']), shell=True).wait()
                    print('Ready to begin rysnc')
                    logging.info('Starting rsync')

                    #copy the files
                    p5 = subprocess.Popen('sudo rsync -a {}/ {}'.format(device['data_dir'],device['temp_dir']), shell=True).wait()

                    logging.info('rsync is complete.  Umounting devices and moving new storage to real location.')

                    #umount the devices
                    p6 = subprocess.Popen('sudo umount {}'.format(device['newdevice']), shell=True).wait()
                    p7 = subprocess.Popen('sudo umount {}'.format(device['blockdevice']), shell=True).wait()

                    #remount the new device under the old directory
                    p8 = subprocess.Popen('sudo mount {} {}'.format(device['newdevice'],device['data_dir']), shell=True).wait()
                    logging.info('New mount completed.  Removing old volume from the instance.')
                    #disconnect the old volume from the host
                    print('Detaching old volume!')
                    detach_response = ec2.detach_volume(Device=device['blockdevice'], Force=False, InstanceId=local.json_simple['instanceid'] ,VolumeId=device_id, DryRun=False)
                    detaching = True
                    while detaching:
                        #make sure the volume detaches from the instance
                        time.sleep(10)
                        detach_response = ec2.describe_volume_status(VolumeIds=[newvolume_response['VolumeId']])
                        if detach_response['VolumeStatuses'][0]['VolumeStatus']['Status']=='ok':
                            detaching=False

                    #detached.  Tag it for prosperity
                    #note:  you can look for any volumes with a Tag of "RecoverMe" and a Value of "Yes" and delete all of them
                    #programmatically
                    tag_response = ec2.create_tags(Resources=[blockdevices['Ebs']['VolumeId']],
                                Tags=[
                                    {'Key': 'ProcessCreatedBy', 'Value': 'EBSShrink'},
                                    {'Key': 'ProcessCreatedTimestamp', 'Value': dt.strftime('%m/%d/%Y %H:%M:%S')},
                                    {'Key': 'OriginalHost', 'Value': local.json_simple['instanceid']},
                                    {'Key': 'ReplacedByVolumeId', 'Value': newvolume_response['VolumeId']},
                                    {'Key': 'OriginalMountPoint', 'Value': device['data_dir']},
                                    {'Key': 'Device', 'Value': device['blockdevice']},
                                    {'Key': 'RecoverMe', 'Value':'Yes'},
                                ]
                            )
                    p9 = subprocess.Popen('sudo rm -r {}'.format(device['temp_dir']), shell=True).wait()
                    print('Process complete!  Mounted new device {} to {} on instance {}'.format(device['newdevice'], device['data_dir'], local.json_simple['instanceid']))
                    logging.info('Process complete!  Mounted new device {} to {} on instance {}'.format(device['newdevice'], device['data_dir'], local.json_simple['instanceid']))
                    break
                if not devicefound:
                    print('Device {} not found on instance {}.  Nothing to do.'.format(device['blockdevice'], local.json_simple['instanceid']))
                    logging.info('Device {} not found on instance {}.  Nothing to do for this device.'.format(device['blockdevice'], local.json_simple['instanceid']))
                    continue
    except Exception as e:
        print(sys.exc_info()[0])
        print("Error {}".format(e))
        exit()
print('Process completed!')
