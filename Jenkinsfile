// Node-RED flow deployment.
//
// Replaces the pipeline inherited from the project template, which deployed a
// FastAPI/Vue stack that no longer exists in this repository. What it is worth
// keeping — the host map and the per-host credential ids — is carried over.
//
// This pipeline deploys. It does not build: GitLab CI validates the registry,
// checks that every committed flow is normalized, and builds and signs the
// images (decision 8). Jenkins is here because it is the only agent with
// network reach into the sites.
//
// The flow deploy runs ON THE TARGET HOST (decision 10). Port publishing is
// inconsistent across the estate and the sites sit in separate subnets, so a
// central agent would reach some instances and not others; from the host every
// instance is at its container address on 1880. SSH carries the script and the
// flow file — it never writes a flow into /data, which would abandon the rev
// handshake that makes concurrent edits visible.
//
// Requires the SSH Pipeline Steps and Pipeline Utility Steps plugins.

import groovy.transform.Field

// Shared across stages. @Field declares them properly; assigning to an
// undeclared name at script level makes Jenkins warn about memory leaks on
// every run, and the warning is right.
@Field def REGISTRY = null
@Field def TARGETS = []
@Field def VISITED = []

pipeline {
    agent any

    options {
        // Two runs at once race each other through the rev handshake: the
        // second reads a rev the first has already replaced, and the deploy
        // aborts on a 409 nobody caused by editing in the browser. Constraint 1
        // stands either way — a real browser edit still fails the pipeline, and
        // there is still no --force. This only stops the pipeline racing itself.
        disableConcurrentBuilds()
    }

    parameters {
        choice(name: 'INSTANCE', description: 'Instance to deploy, or ALL', choices: [
            'ALL',
            'cho-prod', 'cho-test',
            'gor-prod', 'gor-test',
            'jan-prod', 'jan-test',
            'slu-prod', 'slu-test',
            'srem-prod', 'srem-test',
            'wag-prod', 'wag-test',
            'wfm-prod', 'wfm-test',
            // Mid-migration off FlowFuse (decision 12). They carry an app and a
            // registry entry, so ALL already reaches them — without them here a
            // single-instance run, and therefore EXPECT_REV, could not.
            'pod-prod', 'pod-test',
            'dpn-prod', 'dpn-test',
        ])
        booleanParam(name: 'DRY_RUN', defaultValue: true,
            description: 'Print the diff and change nothing. Leave on until the diff is what you expect.')
        string(name: 'EXPECT_REV', defaultValue: '',
            description: 'The rev the dry run reported for this instance. With it, a deploy refuses to write when the instance changed in between — a browser deploy, or another run. Without it, the deploy overwrites whatever it finds. One instance only.')
        booleanParam(name: 'DEPLOY_PALETTE', defaultValue: false,
            description: 'Also pull the pinned image and recreate the service. RESTARTS the container — only needed when apps/<app>/package.json changed.')
    }

    environment {
        REMOTE_DIR = "/tmp/dap-node-red-${BUILD_NUMBER}"
    }

    stages {
        stage('Resolve') {
            steps {
                script {
                    // registry.yml is the single source of truth for what runs
                    // where. Reading it here rather than duplicating it into
                    // this file is what keeps the two from drifting apart.
                    REGISTRY = readYaml file: 'registry.yml'

                    TARGETS = params.INSTANCE == 'ALL'
                        ? REGISTRY.instances.findAll { it.app }
                        : REGISTRY.instances.findAll { it.name == params.INSTANCE }

                    if (!TARGETS) {
                        error("No instance named '${params.INSTANCE}' in registry.yml")
                    }
                    // One rev belongs to one instance, so pinning it across a
                    // fleet run would be a claim about twelve instances made
                    // from one of them.
                    if (params.EXPECT_REV?.trim() && params.INSTANCE == 'ALL') {
                        error('EXPECT_REV is one instance\'s rev — pick that instance, not ALL')
                    }
                    def skipped = TARGETS.findAll { !it.app }
                    if (skipped) {
                        echo "Skipping (no app of their own): ${skipped.collect { it.name }.join(', ')}"
                        TARGETS = TARGETS.findAll { it.app }
                    }

                    // deploy.py reads registry.json, because PyYAML is not
                    // guaranteed on a site host and hand-parsing YAML there
                    // would risk deploying the wrong flow to the wrong instance.
                    writeJSON file: 'registry.json', json: REGISTRY, pretty: 2

                    echo "Deploying: ${TARGETS.collect { it.name }.join(', ')}"
                    echo "Dry run:   ${params.DRY_RUN}"
                    echo "Palette:   ${params.DEPLOY_PALETTE}"
                }
            }
        }

        stage('Deploy') {
            steps {
                script {
                    def mkHost = { name, ip -> [name: name, host: ip, port: 22,
                                                allowAnyHosts: true, credId: "${name}_pw"] }
                    def HOSTS = [
                        'cho-svr-lin01' : mkHost('cho-svr-lin01',  '10.11.1.101'),
                        'pod-svr-lin01' : mkHost('pod-svr-lin01',  '10.20.1.160'),
                        'jan-svr-lin01' : mkHost('jan-svr-lin01',  '10.30.1.150'),
                        'srem-svr-lin01': mkHost('srem-svr-lin01', '10.40.1.161'),
                        'wag-svr-lin01' : mkHost('wag-svr-lin01',  '10.60.1.198'),
                        'foi-svr-lnx01' : mkHost('foi-svr-lnx01',  '192.168.64.60'),
                        'gor-svr-lin01' : mkHost('gor-svr-lin01',  '10.87.1.150'),
                        'slu-svr-lin02' : mkHost('slu-svr-lin02',  '10.90.1.20'),
                        'wfm-svr-lin01' : mkHost('wfm-svr-lin01',  '10.65.1.91'),
                        // Runs flowfuse/device-agent today; joins once migrated
                        // to a plain container (decision 12).
                        'dpn-svr-iot'   : mkHost('dpn-svr-iot',    '192.168.48.240'),
                    ]

                    // One instance at a time, and a failure stops the run. A
                    // deploy that carries on past a failure leaves the estate
                    // in a state nobody chose.
                    for (inst in TARGETS) {
                        deployInstance(inst, HOSTS)
                    }
                }
            }
        }
    }

    post {
        always {
            script {
                // The env file carries the instance's Admin API password. It is
                // removed from the workspace and from every host it reached,
                // whatever happened above.
                sh 'rm -f deploy.env registry.json || true'
                VISITED.each { remote ->
                    sshCommand remote: remote, failOnError: false,
                               command: "rm -rf ${REMOTE_DIR}"
                }
            }
        }
        success {
            echo params.DRY_RUN
                ? 'Dry run complete — nothing was changed.'
                : 'Deployment complete.'
        }
    }
}

// ---------------------------------------------------------------------------

void deployInstance(Map inst, Map hosts) {
    def hostConfig = hosts[inst.host]
    if (!hostConfig) {
        error("${inst.name}: host '${inst.host}' is not in the Jenkins host map")
    }

    withCredentials([
        usernamePassword(credentialsId: hostConfig.credId,
                         usernameVariable: 'REMOTE_USR', passwordVariable: 'REMOTE_PSW'),
        usernamePassword(credentialsId: inst.auth_credential_id,
                         usernameVariable: 'NR_USR', passwordVariable: 'NR_PSW'),
    ]) {
        def remote = [
            name: inst.host, host: hostConfig.host, port: hostConfig.port,
            allowAnyHosts: hostConfig.allowAnyHosts,
            user: REMOTE_USR, password: REMOTE_PSW,
        ]
        if (!VISITED.any { it.host == remote.host }) { VISITED << remote }

        // deploy.py takes credentials from the environment, never from an
        // argument — an argument is visible in `ps` to anyone on the host.
        // The stem is what nodered.credential_stem() builds on the other side.
        def stem = inst.auth_credential_id.replaceAll(/[^A-Za-z0-9]/, '_').toUpperCase()
        writeFile file: 'deploy.env', text: """\
            export ${stem}_USR='${NR_USR}'
            export ${stem}_PSW='${NR_PSW}'
            """.stripIndent()

        sshCommand remote: remote, command: "mkdir -p ${REMOTE_DIR}/scripts ${REMOTE_DIR}/apps/${inst.app}"
        sshPut remote: remote, from: 'scripts/deploy.py',    into: "${REMOTE_DIR}/scripts/"
        sshPut remote: remote, from: 'scripts/nodered.py',   into: "${REMOTE_DIR}/scripts/"
        sshPut remote: remote, from: 'scripts/normalize.py', into: "${REMOTE_DIR}/scripts/"
        sshPut remote: remote, from: 'registry.json',        into: "${REMOTE_DIR}/"
        sshPut remote: remote, from: "apps/${inst.app}/flows.json",
               into: "${REMOTE_DIR}/apps/${inst.app}/"
        sshPut remote: remote, from: 'deploy.env', into: "${REMOTE_DIR}/"

        def dryRun = params.DRY_RUN ? '--dry-run' : ''
        // Read in the dry run, approved by a human, pinned here: an edit made
        // between the two stops the write instead of being flattened by it.
        def expect = params.EXPECT_REV?.trim() ? "--expect-rev ${params.EXPECT_REV.trim()}" : ''
        // The env file is sourced and deleted in the same shell, so the
        // password never rests on the host between steps. `set +x` keeps it
        // out of the trace; deploy.py itself never echoes it.
        sshCommand remote: remote, command: """
            set -e
            cd ${REMOTE_DIR}
            chmod 600 deploy.env
            set +x
            . ./deploy.env
            rm -f deploy.env
            python3 scripts/deploy.py --instance ${inst.name} ${dryRun} ${expect}
        """

        if (params.DEPLOY_PALETTE && !params.DRY_RUN) {
            // Named service, always. The compose file may hold others, and a
            // bare `docker compose up -d` would recreate them too.
            sshCommand remote: remote, command: """
                set -e
                docker pull ${inst.image_tag}
                docker compose -f ${inst.compose_file} up -d ${inst.compose_service}
            """
        }
    }
}
