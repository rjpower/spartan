#ifndef MASTER_H_
#define MASTER_H_

#include "spartan/kernel.h"
#include "spartan/table.h"
#include "spartan/util/common.h"
#include "spartan/util/timer.h"
#include "spartan/spartan_service.h"

#include "rpc/utils.h"

#include <vector>
#include <map>
#include <boost/noncopyable.hpp>

namespace spartan {

class WorkerState;
class TaskState;

struct RunDescriptor {
  Table *table;

  int kernel_id;
  Kernel::ArgMap args;
  std::vector<int> shards;
};

typedef std::pair<int, int> ShardId;
class TaskState {
public:
  TaskState() :
      size(-1), stolen(false) {

  }
  TaskState(ShardId id, int64_t size) :
      id(id), size(size), stolen(false) {
  }

  ShardId id;
  int size;
  bool stolen;
};

typedef std::map<ShardId, TaskState> TaskMap;
typedef std::set<ShardId> ShardSet;

class WorkerState: private boost::noncopyable {
public:
  TaskMap pending;
  TaskMap active;
  TaskMap finished;

  ShardSet shards;

  int status;
  int id;

  double last_ping_time;
  double total_runtime;

  bool alive;

  WorkerProxy *proxy;
  HostPort addr;

  mutable rpc::Mutex lock;

  WorkerState(int w_id, HostPort addr) :
      id(w_id) {
    last_ping_time = Now();
    total_runtime = 0;
    alive = true;
    status = 0;
    proxy = NULL;
    this->addr = addr;
  }

  bool is_assigned(ShardId id) {
    rpc::ScopedLock sl(&lock);

    return pending.find(id) != pending.end();
  }

  bool serves_shard(int shard) {
    for (auto sid : shards) {
      if (sid.second == shard) {
        return true;
      }
    }
    return false;
  }

  void ping() {
    last_ping_time = Now();
  }

  double idle_time() {
    return Now() - last_ping_time;
  }

  void assign_task(ShardId id) {
    rpc::ScopedLock sl(&lock);

    TaskState state(id, 1);
    pending[id] = state;
  }

  void remove_task(ShardId id) {
    rpc::ScopedLock sl(&lock);

    pending.erase(pending.find(id));
  }

  void clear_tasks() {
    rpc::ScopedLock sl(&lock);

    CHECK(active.empty());
    pending.clear();
    active.clear();
    finished.clear();
  }

  void set_finished(const ShardId& id) {
    rpc::ScopedLock sl(&lock);

    finished[id] = active[id];
    active.erase(active.find(id));
  }

  std::string str() {
    rpc::ScopedLock sl(&lock);

    return StringPrintf("W(%d) p: %d; a: %d f: %d", id, pending.size(),
        active.size(), finished.size());
  }

  int num_assigned() const {
    rpc::ScopedLock sl(&lock);
    return pending.size() + active.size() + finished.size();
  }

  int num_pending() const {
    return pending.size();
  }

  // Order pending tasks by our guess of how large they are
  bool get_next(const RunDescriptor& r, RunKernelReq* msg) {
    rpc::ScopedLock sl(&lock);

    if (pending.empty()) {
      return false;
    }

    if (!active.empty()) {
      return false;
    }

    TaskState state = pending.begin()->second;
    active[state.id] = state;
    pending.erase(pending.begin());

    msg->kernel = r.kernel_id;
    msg->table = r.table->id();
    msg->shard = state.id.second;
    msg->args = r.args;

    return true;
  }
};

Master* start_master(int port, int num_workers);

class Master: public TableContext, public MasterService {
public:
  Master(int num_workers);
  ~Master();

  void wait_for_workers();

  // TableHelper implementation
  int id() const {
    return -1;
  }

  void shutdown();

  void flush();

  void destroy_table(int table_id);

  void destroy_table(Table* t) {
    destroy_table(t->id());
  }

  int num_workers() {
    return num_workers_;
  }

  template<class K, class V>
  TableT<K, V>* create_table(
      SharderT<K>* sharder = new Modulo<K>(),
      AccumulatorT<V>* combiner = new Replace<V>(),
      AccumulatorT<V>* reducer = new Replace<V>(),
      SelectorT<K, V>* selector = NULL) {
    wait_for_workers();

    TableT<K, V>* t = new TableT<K, V>();

    // Crash here if we can't find the sharder/accumulators.
    delete TypeRegistry<Sharder>::get_by_id(sharder->type_id());

    CreateTableReq req;
    int table_id = table_id_counter_++;

    Log_debug("Creating table %d", table_id);
    req.table_type = t->type_id();
    req.id = table_id;
    req.num_shards = workers_.size() * 2 + 1;

    if (combiner != NULL) {
      delete TypeRegistry<Accumulator>::get_by_id(combiner->type_id());
      req.combiner.type_id = combiner->type_id();
      req.combiner.opts = combiner->opts();
    } else {
      req.combiner.type_id = -1;
    }

    if (reducer != NULL) {
      delete TypeRegistry<Accumulator>::get_by_id(reducer->type_id());
      req.reducer.type_id = reducer->type_id();
      req.reducer.opts = reducer->opts();
    } else {
      req.reducer.type_id = -1;
    }

    req.sharder.type_id = sharder->type_id();
    req.sharder.opts = sharder->opts();

    if (selector != NULL) {
      req.selector.type_id = selector->type_id();
      req.selector.opts = selector->opts();
    } else {
      req.selector.type_id = -1;
    }

    t->init(table_id, req.num_shards);
    t->sharder = sharder;
    t->combiner = combiner;
    t->reducer = reducer;
    t->selector = selector;

    t->workers.resize(workers_.size());
    for (auto w : workers_) {
      t->workers[w->id] = w->proxy;
    }

    t->set_ctx(this);

    tables_[t->id()] = t;

    rpc::FutureGroup futures;
    for (auto w : workers_) {
      futures.add(w->proxy->async_create_table(req));
    }
    futures.wait_all();

    assign_shards(t);
    return t;
  }

  void map_shards(Table* t, const std::string& kernel) {
    map_shards(t, TypeRegistry<Kernel>::get_by_name(kernel));
  }

  void map_shards(Table* t, Kernel* k) {
    RunDescriptor r;
    r.kernel_id = k->type_id();
    r.args = k->args();
    r.table = t;
    r.shards = range(0, t->num_shards());

    run(r);
  }

  void run(RunDescriptor r);

  Table* get_table(int id) const {
    return tables_.find(id)->second;
  }

private:
  void register_worker(const RegisterReq& req);

  // Find a worker to run a kernel on the given table and shard.  If a worker
  // already serves the given shard, return it.  Otherwise, find an eligible
  // worker and assign it to them.
  WorkerState* assign_shard(int table, int shard);

  void send_table_assignments();
  void assign_shards(Table *t);
  void assign_tasks(const RunDescriptor& r, std::vector<int> shards);
  int dispatch_work(const RunDescriptor& r);
  int num_pending(const RunDescriptor& r);

  RunDescriptor current_run_;
  double current_run_start_;

  int num_workers_;
  std::vector<WorkerState*> workers_;

  rpc::Mutex lock_;
  std::map<int, rpc::Future*> running_kernels_;

  rpc::PollMgr *client_poller_;
  TableMap tables_;
  Timer runtime_;

  bool initialized_;
  int table_id_counter_;
};

}

#endif /* MASTER_H_ */
