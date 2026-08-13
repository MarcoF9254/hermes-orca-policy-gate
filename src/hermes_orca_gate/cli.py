'''Compact JSON command-line interface for the deterministic gate lifecycle.'''
from __future__ import annotations
import argparse,hashlib,json,os,shutil,sys,time,uuid
from datetime import datetime,timezone
from pathlib import Path
from .canonical import canonical_digest
from .errors import DeniedError,GateError,InputError
from .models import TypedRequest
from .orca_cli import OrcaAdapter,OrcaDiscovery
from .policy import Policy
from .service import GateExecutor,GateService,ObservationController,StaticIdentityResolver
from .stores import GateStore

SOURCE_CONTRACT_SHA='403c60bbadb734d870a22c372aca8a988e348d21'

class Parser(argparse.ArgumentParser):
    def error(self,message): raise InputError(message,details={'invalid_invocation':True})

def _parser():
    parser=Parser(prog='hermes-orca-gate',description='Deterministic Hermes to Orca policy gate')
    commands=parser.add_subparsers(dest='subcommand',required=True)
    validate=commands.add_parser('validate-policy');validate.add_argument('--policy',required=True)
    init=commands.add_parser('init-state');init.add_argument('--state',required=True)
    status=commands.add_parser('status');status.add_argument('--state',required=True)
    preflight=commands.add_parser('preflight');_common(preflight);preflight.add_argument('--request',required=True);preflight.add_argument('--recover-intent')
    authorize=commands.add_parser('owner-authorize');authorize.add_argument('--state',required=True);authorize.add_argument('--decision',required=True);authorize.add_argument('--kind',choices=('owner_approval','owner_reconciliation'),required=True);authorize.add_argument('--scope');authorize.add_argument('--reconciliation-action',choices=('receipt_recovery','manual_break_glass'));authorize.add_argument('--observation-deadline-ms',type=int)
    resolve=commands.add_parser('resolve-approval');_common(resolve);resolve.add_argument('--authorization',required=True);resolve.add_argument('--request',required=True)
    execute=commands.add_parser('execute');_common(execute);execute.add_argument('--operation',required=True);execute.add_argument('--request',required=True)
    observe=commands.add_parser('observe');observe.add_argument('--policy',required=True);observe.add_argument('--state',required=True);observe.add_argument('--dispatch',required=True);observe.add_argument('--continue-authorization')
    reconcile=commands.add_parser('reconcile');reconcile.add_argument('--state',required=True);reconcile.add_argument('--scope',required=True);reconcile.add_argument('--authorization',required=True);reconcile.add_argument('--evidence',required=True)
    return parser

def _common(parser):
    parser.add_argument('--policy',required=True);parser.add_argument('--state',required=True)

def _read(path):
    with open(path,'r',encoding='utf-8') as stream:return json.load(stream,object_pairs_hook=_pairs)

def _pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise InputError('duplicate JSON field: '+key)
        result[key]=value
    return result

def _runtime_context():
    executable=shutil.which('orca')
    data=os.environ.get('ORCA_USER_DATA_PATH');dev=os.environ.get('ORCA_DEV_CLI_INVOCATION');credential=os.environ.get('HERMES_ORCA_GATE_CREDENTIAL_BINDING_ID')
    if not executable or data is None or dev not in ('0','1') or not credential:raise InputError('controlled Orca identity environment is incomplete')
    executable_path=Path(executable).resolve()
    try:executable_sha=hashlib.sha256(executable_path.read_bytes()).hexdigest()
    except OSError as exc:raise InputError(f'cannot bind Orca executable identity: {exc}') from exc
    target=canonical_digest({'orca_user_data_path':str(Path(data).resolve())})
    return {'cli_executable_identity':f'{executable_path}::sha256:{executable_sha}','source_contract_sha':SOURCE_CONTRACT_SHA,'effective_target_identity':target,'fixed_or_bound_cwd':str(Path.cwd().resolve()),'execution_relevant_env':{'ORCA_USER_DATA_PATH':data,'ORCA_DEV_CLI_INVOCATION':dev},'target_instance_identity':target,'dev_mode':dev=='1','credential_binding_id':credential}

def _epoch_id():
    value=os.environ.get('HERMES_ORCA_GATE_PROCESS_EPOCH_ID')
    if not value:raise InputError('controlled process epoch identity is missing')
    return value

def _request(path, intent=None):
    raw=_read(path)
    if not isinstance(raw,dict) or set(raw)!={'command','args'}:raise InputError('CLI request contains only command and args')
    context=_runtime_context()
    if intent is not None:
        context['mutation_intent_identity_or_null']={
            'mutation_intent_id':intent['mutation_intent_id'],
            'orca_request_id':intent['orca_request_id'],
            'normalized_method':intent['method'],
            'effective_payload_digest':intent['payload_digest'],
            'credential_binding_id':intent['credential_binding_id'],
        }
    return TypedRequest.from_dict({'command':raw['command'],'args':raw['args'],'context':context})

def _state(path):
    store=GateStore(path);store.initialize();store.startup_sweep();return store

def _emit(code,result=None):
    print(json.dumps({'code':code,'result':result},ensure_ascii=False,sort_keys=True,separators=(',',':')));return 0

def _run(options):
    command=options.subcommand
    if command=='validate-policy':
        policy=Policy.from_path(options.policy);return _emit('POLICY_VALID',{'policy_sha256':policy.sha256,'source_contract_sha':policy.data['source_contract_sha']})
    if command=='init-state':
        store=_state(options.state);return _emit('STATE_INITIALIZED',{'path':str(Path(options.state).resolve()),'pragmas':store.pragmas()})
    if command=='status':return _emit('STATUS',_state(options.state).status())
    if command=='owner-authorize':
        store=_state(options.state);basis=store.get_decision(options.decision)
        if not basis:raise InputError('basis decision does not exist')
        observation_continuation=basis.get('normalized_command')=='observation.continue'
        if options.kind=='owner_approval' and (basis['action']!='require_approval' or options.reconciliation_action):raise DeniedError('ordinary approval cannot override this decision')
        if options.kind=='owner_approval' and observation_continuation and (type(options.observation_deadline_ms) is not int or options.observation_deadline_ms<=time.monotonic_ns()//1_000_000):raise InputError('Owner-selected future observation deadline is required')
        if options.kind=='owner_approval' and not observation_continuation and options.observation_deadline_ms is not None:raise InputError('observation deadline is not admitted for this decision')
        if options.kind=='owner_reconciliation' and (basis['action']!='reconciliation_required' or not options.scope or not store.reconciliation_required(options.scope)):raise DeniedError('exact open reconciliation decision and scope are required')
        if options.kind=='owner_reconciliation' and (not options.reconciliation_action or options.observation_deadline_ms is not None):raise InputError('exact reconciliation action is required')
        authorization_id=str(uuid.uuid4())
        store.append_owner_authorization(authorization_id,options.decision,{'authorization_kind':options.kind,'exact_action':basis['normalized_command'],'args_digest':basis['args_digest'],'approved_state_class':basis['state_class'],'mutation_intent_id':basis.get('mutation_intent_id'),'reconciliation_scope':options.scope,'reconciliation_action':options.reconciliation_action,'observation_scope':basis.get('observation_scope'),'observation_deadline_ms':options.observation_deadline_ms,'issued_at':datetime.now(timezone.utc).isoformat()})
        return _emit('OWNER_AUTHORIZATION_RECORDED',{'authorization_id':authorization_id})
    if command=='observe':
        store=_state(options.state);policy=Policy.from_path(options.policy);resolver=StaticIdentityResolver(_runtime_context());adapter=OrcaAdapter()
        controller=ObservationController(store,policy,adapter,resolver=resolver,epoch_id=_epoch_id(),clock=lambda:time.monotonic_ns()//1_000_000)
        if options.continue_authorization:controller.continue_observation(options.dispatch,options.continue_authorization)
        return _emit('OBSERVATION_RESULT',controller.run(options.dispatch))
    if command=='reconcile':
        store=_state(options.state);authorization=store.get_owner_authorization(options.authorization)
        if not authorization or authorization.get('authorization_kind')!='owner_reconciliation' or authorization.get('reconciliation_scope')!=options.scope or authorization.get('reconciliation_action')!='manual_break_glass':raise InputError('exact Owner manual reconciliation authorization required')
        reviewed=_read(options.evidence)
        required={'evidence_kind','scope','finding','source_contract_sha'}
        if not isinstance(reviewed,dict) or set(reviewed)!=required or reviewed.get('evidence_kind')!='independently_sourced_owner_evidence' or reviewed.get('scope')!=options.scope or reviewed.get('finding') not in ('locality_established','no_effect_confirmed','contained') or reviewed.get('source_contract_sha')!=SOURCE_CONTRACT_SHA:raise InputError('manual reconciliation evidence schema or binding is invalid')
        evidence={'evidence_kind':'owner_accepted_manual_break_glass','owner_reviewed_evidence':reviewed};store.close_reconciliation(options.scope,options.authorization,evidence);return _emit('RECONCILIATION_CLOSED_MANUAL',{'scope':options.scope,'evidence_kind':evidence['evidence_kind']})
    policy=Policy.from_path(options.policy);store=_state(options.state);resolver=StaticIdentityResolver(_runtime_context());adapter=OrcaAdapter();discovery=OrcaDiscovery(adapter,provenance_resolver=store.require_local_provenance);service=GateService(store,policy,resolver=resolver,epoch_id=_epoch_id(),discovery=discovery)
    if command=='preflight':
        request=_request(options.request)
        result=service.preflight(request,recovery_intent_id=options.recover_intent)
        identity=result['request'].context.get('mutation_intent_identity_or_null') or {}
        encoded={'decision':result['decision'],'decision_event_id':result['decision_event_id'],'args_digest':result['request'].args_digest,'operation_id':result['invocation'].operation_id if result['invocation'] else None,'mutation_intent_id':identity.get('mutation_intent_id'),'orca_request_id':identity.get('orca_request_id'),'discovery':result['discovery']}
        return _emit('PREFLIGHT_RESULT',encoded)
    if command=='resolve-approval':
        authorization=store.get_owner_authorization(options.authorization)
        if not authorization:raise InputError('Owner authorization does not exist')
        intent=store.get_mutation_intent(authorization['mutation_intent_id']) if authorization.get('mutation_intent_id') else None
        request=_request(options.request,intent)
        invocation=service.resolve_approval(options.authorization,request)
        return _emit('APPROVAL_RESOLVED',{'operation_id':invocation.operation_id})
    if command=='execute':
        invocation=store.get_approved_invocation(options.operation)
        if not invocation:raise InputError('ApprovedInvocation does not exist')
        intent=store.get_mutation_intent(invocation['mutation_intent_id']) if invocation.get('mutation_intent_id') else None
        request=_request(options.request,intent);executor=GateExecutor(store,policy,adapter,resolver=resolver,epoch_id=_epoch_id())
        return _emit('EXECUTION_RESULT',executor.execute(options.operation,request,now_mono_ms=time.monotonic_ns()//1_000_000))
    raise InputError('unknown subcommand')

def main(argv=None):
    try:return _run(_parser().parse_args(argv))
    except GateError as exc:
        code='INVALID_INVOCATION' if exc.details.get('invalid_invocation') else exc.code
        print(json.dumps({'code':code,'message':str(exc),'details':exc.details},ensure_ascii=False,sort_keys=True,separators=(',',':')));return exc.exit_code
    except (OSError,ValueError,KeyError) as exc:
        print(json.dumps({'code':'INVALID_INPUT','message':str(exc)},sort_keys=True,separators=(',',':')));return 2

if __name__=='__main__':raise SystemExit(main())
