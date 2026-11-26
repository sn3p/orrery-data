#!/usr/bin/env python3
"""Build an immutable unpublished candidate; cannot write to the publication tree."""
import argparse
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from orrery_data.named_bodies import validate_definitions, need
from orrery_data.named_body_mapping import build_mapping
from orrery_data.storage import read_json, json_bytes, file_info, writer_lock, rename_new
from orrery_data.browser import bytes_reference
ROOT=Path(__file__).resolve().parents[1]


def build(export, browser, output, definitions=ROOT/'research/named-bodies/definitions.json'):
    namespace=ROOT/'.context/named-body-candidates';output=Path(output).absolute()
    need(namespace.resolve()==namespace and not output.is_symlink() and output.parent.resolve()==namespace, 'candidate destination')
    definitions=validate_definitions(read_json(Path(definitions)))
    mapping=build_mapping(export,browser)
    raw=json_bytes(definitions); dref=bytes_reference(raw,'definitions-')
    mraw=json_bytes(mapping); mref=bytes_reference(mraw,'mapping-')
    files={dref['url']:raw,mref['url']:mraw,'index.json':json_bytes(dict(named_body_product_version=1,definitions=dref,mapping=mref))}
    namespace.mkdir(parents=True,exist_ok=True)
    with writer_lock(namespace):
        need(namespace.resolve()==namespace and not output.is_symlink() and output.parent.resolve()==namespace, 'candidate destination changed')
        if output.exists():
            need(output.is_dir() and {p.name for p in output.iterdir()}==set(files), 'existing candidate inventory')
            need(all(not (output/n).is_symlink() and (output/n).is_file() and (output/n).read_bytes()==raw for n,raw in files.items()), 'immutable candidate differs')
        else:
            with tempfile.TemporaryDirectory(dir=namespace) as temp:
                stage=Path(temp)/'product';stage.mkdir()
                for name,raw in files.items():(stage/name).write_bytes(raw)
                rename_new(stage,output)
    return {'path':str(output),'index':dict(url='index.json',**file_info(output/'index.json')),'mapping':mapping}

if __name__=='__main__':
    import json
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--export',type=Path,required=True);p.add_argument('--browser',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(build(a.export,a.browser,a.output)))
