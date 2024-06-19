import os
import json
import subprocess
from urllib.request import urlopen

import psutil

import re

ABS_PATH = os.path.dirname(os.path.realpath(__file__))
HOSTS_DB = os.path.join(ABS_PATH, 'gpuhosts.db')

def extract_numbers(s):
    return [int(d) if d.isdigit() else d for d in re.split(r'(\d+)', s)]

def get_cpu_name_linux():
    with open('/proc/cpuinfo') as f:
        for line in f:
            if 'model name' in line:
                return line.split(':')[1].strip()

def get_container_info(pid):
    try:
        cmd_get_container_id = "cat /proc/" + pid + "/cgroup | grep -oP '/docker/\K.{12}'"
        container_id = subprocess.check_output(cmd_get_container_id, shell=True, text=True).strip()

        cmd_parts = ["docker", "inspect", "--format", "{{.Name}}", container_id]
        cmd_get_container_name = " ".join(cmd_parts)
        container_name = subprocess.check_output(cmd_get_container_name, shell=True, text=True).strip()

        cmd_parts = ["ps -p ", pid, " -o etimes --no-headers"]
        cmd_get_running_time = " ".join(cmd_parts)
        elapsed_time = subprocess.check_output(cmd_get_running_time, shell=True, text=True).strip()

        return container_name, elapsed_time
    except subprocess.CalledProcessError as e:
        print(f"Error: {e}")
        return None, None

def get_process_info(pid):
    try:
        process = psutil.Process(pid)
        cpu_usage = process.cpu_percent(interval=1)
        memory_info = process.memory_info()
        memory_usage = memory_info.rss / (1024 * 1024 * 1024)
        return cpu_usage, memory_usage
    except psutil.NoSuchProcess:
        return None, None

def get_disk_info():
    partitions = psutil.disk_partitions()
    ssd_info = []
    data_info = []
    system_info = {}

    for partition in partitions:
        usage = psutil.disk_usage(partition.mountpoint)
        disk_info = {
            'mountpoint': partition.mountpoint,
            'total': round(usage.total / (1024 ** 4), 1),
            'used': round(usage.used / (1024 ** 4), 1),
            'free': round(usage.free / (1024 ** 4), 1),
            'percent': round(usage.percent, 1)
        }

        if partition.mountpoint.startswith('/ssd'):
            ssd_info.append(disk_info)
        elif partition.mountpoint.startswith('/data'):
            data_info.append(disk_info)
        elif partition.mountpoint == '/':
            system_info = disk_info

    sorted_ssd_info = sorted(ssd_info, key=lambda g: g['mountpoint'])
    sorted_data_info = sorted(data_info, key=lambda g: g['mountpoint'])

    return sorted_ssd_info, sorted_data_info, system_info

def my_gpustat():
    """
    Returns a [safe] version of gpustat for this host.
        # Omit sensitive details, eg. uuid, username, and processes.
        # Set color flag based on gpu temperature:
            # bg-warning, bg-danger, bg-success, bg-primary

    Returns:
        dict: gpustat
    """

    try:
        from gpustat import GPUStatCollection
        stat = GPUStatCollection.new_query().jsonify()
        delete_list = []
        
        cpu_name = get_cpu_name_linux()
        cpu_count_logical = psutil.cpu_count(logical=True)
        server_cpu_usage = psutil.cpu_percent(interval=1)
        
        mem = psutil.virtual_memory()

        for gpu_id, gpu in enumerate(stat['gpus']):
            if type(gpu['processes']) is str:
                delete_list.append(gpu_id)
                continue
            gpu['memory'] = round(float(gpu['memory.used']) /
                                  float(gpu['memory.total']) * 100)
            gpu['users'] = 0
            useful_process = []
            for p in gpu['processes']:
                pid = str(p['pid'])
                c_name, e_time = get_container_info(pid)
                if c_name:
                    hrs = str(round(int(e_time) / 3600, 1))                    
                    process_str = '%s(%sh, %sG)' % (c_name.split("/")[-1], hrs, round(p['gpu_memory_usage'] / 1024, 1))
                    cpu_usage, memory_usage = get_process_info(int(pid))
                    process_str += ' (CPU: {}%, Mem: {}GB)'.format(round(cpu_usage, 1), round(memory_usage, 1))
                    useful_process.append(process_str)
                    gpu['users'] = len(useful_process)
                    
            gpu['user_processes'] = ' '.join(useful_process)

            gpu['flag'] = 'bg-success'
            if gpu['temperature.gpu'] > 80:
                gpu['flag'] = 'bg-danger'
            elif gpu['temperature.gpu'] > 60:
                gpu['flag'] = 'bg-warning'

        if delete_list:
            for gpu_id in delete_list:
                stat['gpus'].pop(gpu_id)
        
        used_memory_gb = round(mem.used / (1024 * 1024 * 1024), 1)
        total_memory_gb = round(mem.total / (1024 * 1024 * 1024), 1)
        mem_usage = round(used_memory_gb / total_memory_gb * 100, 1)
        
        stat['mem_usage'] = str(mem_usage)
        stat['used_mem'] = str(used_memory_gb)
        stat['total_mem'] = str(total_memory_gb) + "GiB"
        stat['cpu_name'] = cpu_name + "(" + str(cpu_count_logical) + ")"
        stat['cpu_stat'] = str(server_cpu_usage)
        
        ssd_info, data_info, system_info = get_disk_info()
        stat['ssd_disks'] = ssd_info
        stat['data_disks'] = data_info
        stat['system_disk_total'] = system_info['total']
        stat['system_disk_used'] = system_info['used']
        stat['system_disk_percent'] = system_info['percent']

        return stat
    except Exception as e:
        return {'error': '%s!' % getattr(e, 'message', str(e))}


def all_gpustats():
    """
    Aggregates the gpustats of all registered hosts and this host.

    Returns:
        list: pustats of hosts
    """

    gpustats = []
    mystat = my_gpustat()
    if 'gpus' in mystat:
        gpustats.append(mystat)

    hosts = load_hosts()
    for url in hosts:
        try:
            raw_resp = urlopen(url + '/gpustat')
            gpustat = json.loads(raw_resp.read())
            raw_resp.close()
            if not gpustat or 'gpus' not in gpustat:
                continue
            if hosts[url] != url:
                gpustat['hostname'] = hosts[url]
            gpustats.append(gpustat)
        except Exception as e:
            print('Error: %s getting gpustat from %s' %
                  (getattr(e, 'message', str(e)), url))

    try:
        sorted_gpustats = sorted(gpustats, key=lambda g: extract_numbers(g['hostname']))
        if sorted_gpustats is not None:
            return sorted_gpustats
    except Exception as e:
        print("Error: %s" % getattr(e, 'message', str(e)))
    return gpustats


def load_hosts():
    """
    Loads the list of registered gpu nodes from file.

    Returns:
        dict: {url: name, ... }
    """

    hosts = {}
    if not os.path.exists(HOSTS_DB):
        print("There are no registered hosts! Use `gpuview add` first.")
        return hosts

    for line in open(HOSTS_DB, 'r'):
        try:
            name, url = line.strip().split('\t')
            hosts[url] = name
        except Exception as e:
            print('Error: %s loading host: %s!' %
                  (getattr(e, 'message', str(e)), line))
    return hosts


def save_hosts(hosts):
    with open(HOSTS_DB, 'w') as f:
        for url in hosts:
            f.write('%s\t%s\n' % (hosts[url], url))


def add_host(url, name=None):
    url = url.strip().strip('/')
    if name is None:
        name = url
    hosts = load_hosts()
    hosts[url] = name
    save_hosts(hosts)
    print('Successfully added host!')


def remove_host(url):
    hosts = load_hosts()
    if hosts.pop(url, None):
        save_hosts(hosts)
        print("Removed host: %s!" % url)
    else:
        print("Couldn't find host: %s!" % url)


def print_hosts():
    hosts = load_hosts()
    if len(hosts):
        hosts = sorted(hosts.items(), key=lambda g: g[1])
        print('#   Name\tURL')
        for idx, host in enumerate(hosts):
            print('%02d. %s\t%s' % (idx+1, host[1], host[0]))


def install_service(host=None, port=None):
    arg = ''
    if host is not None:
        arg += '--host %s ' % host
    if port is not None:
        arg += '--port %s ' % port
    script = os.path.join(ABS_PATH, 'service.sh')
    subprocess.call('{} "{}"'.format(script, arg.strip()), shell=True)
