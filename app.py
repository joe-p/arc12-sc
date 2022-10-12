from pyteal import *
from beaker import *
from beaker.lib.storage import Mapping

algod_client = sandbox.get_algod_client()

# Use a box per member to denote membership parameters

class MasterReceiverVault(abi.NamedTuple):
    app_id: abi.Field[abi.Uint64]
    mbr: abi.Field[abi.Uint64]

class ReceiverVault(abi.NamedTuple):
    sender: abi.Field[abi.Address]
    mbr: abi.Field[abi.Uint64]

class Child(Application):
    vault = Mapping(abi.Uint64, ReceiverVault)
    
    @external
    def init_vault(self, asset: abi.Asset, account: abi.Account):
        return Seq(
            (sender := abi.Address()).set(account.address()),
            (mbr := abi.Uint64()).set(Int(119700)),
            (v := ReceiverVault()).set(sender, mbr),
            (asset_id := abi.Uint64()).set(asset.asset_id()),
            self.vault[asset_id].set(v),
        )
    @external
    def opt_in(self, asset: abi.Asset):
        return InnerTxnBuilder.Execute(
            {
                TxnField.type_enum: TxnType.AssetTransfer,
                TxnField.xfer_asset: asset.asset_id(),
                TxnField.asset_receiver: self.address,
                TxnField.asset_amount: Int(0),
            }
        )

    @external
    def close_out(self, account: abi.Account, asset: abi.Asset):
        return InnerTxnBuilder.Execute(
                {
                TxnField.type_enum: TxnType.AssetTransfer,
                TxnField.xfer_asset: asset.asset_id(),
                TxnField.asset_receiver: account.address(),
                TxnField.asset_amount: Int(0),
                TxnField.asset_close_to: account.address(),
                }
        )

    @external
    def pop_asset(self, account: abi.Account, asset: abi.Asset):
        return Seq(
                (asset_id := abi.Uint64()).set(asset.asset_id()),
                self.vault[asset_id].store_into(v := ReceiverVault()),
                (sender := abi.Address()).set(v.sender),
                (mbr := abi.Uint64()).set(v.mbr),
                Pop(self.vault[asset_id].delete()),
                InnerTxnBuilder.Begin(),
                InnerTxnBuilder.SetFields(
                {
                    TxnField.type_enum: TxnType.Payment,
                    TxnField.amount: mbr.get(),
                    TxnField.receiver: account.address(),
                }),
                InnerTxnBuilder.Submit(),
            )

    @external
    def delete(self, account: abi.Account):
        return InnerTxnBuilder.Execute(
                {
                    TxnField.type_enum: TxnType.Payment,
                    TxnField.amount: Int(0),
                    TxnField.receiver: account.address(),
                    TxnField.close_remainder_to: account.address(),
                }
        )

class Master(Application):
    vault = Mapping(abi.Address, MasterReceiverVault)

    sub_app = Child()
    sub_app_approval: Precompile = Precompile(sub_app.approval_program, algod_client=algod_client)
    sub_app_clear: Precompile = Precompile(sub_app.clear_program, algod_client=algod_client)

    @internal(TealType.uint64)
    def create_sub(self):
        return Seq(
            InnerTxnBuilder.Execute(
                {
                    TxnField.type_enum: TxnType.ApplicationCall,
                    TxnField.approval_program: self.sub_app_approval.binary_bytes,
                    TxnField.clear_state_program: self.sub_app_clear.binary_bytes,
                }
            ),
            InnerTxn.created_application_id(),
        )

    @internal
    def init_vault(self, receiver: abi.Address):
        return Seq(
            (app_id := abi.Uint64()).set(self.create_sub()),
            (mbr := abi.Uint64()).set(Int(1000)),
            (v := MasterReceiverVault()).set(app_id, mbr),
            self.vault[receiver].set(v),
        )

    @external
    def opt_in(self, app: abi.Application, receiver: abi.Account, asset: abi.Asset):
        return Seq(
            app_address := app.params().address(),
            holding := asset.holding(app_address.value()).balance(),
            (opted_in := abi.Uint64()).set(holding.hasValue()),
            Assert(Not(opted_in.get())),
            self.vault[receiver.address()].store_into(v := MasterReceiverVault()),
            InnerTxnBuilder.Begin(),
            InnerTxnBuilder.MethodCall(
                app_id=app.application_id(),                
                method_signature=application.get_method_signature(Child.opt_in),
                args=[asset],
            ),
            InnerTxnBuilder.Next(),
            InnerTxnBuilder.MethodCall(
                app_id=app.application_id(),                
                method_signature=application.get_method_signature(Child.init_vault),
                args=[asset, Txn.sender()],

            ),
            InnerTxnBuilder.Submit(),
        )

    @external
    def get_receiver_vault_app_id(self, receiver: abi.Account, *, output: abi.Uint64):
        return Seq(
            self.vault[receiver.address()].store_into(v := MasterReceiverVault()),
            v.app_id.store_into(output),
        )

    @external
    def receive(self, app: abi.Application, asset: abi.Asset, creator: abi.Account):
        return Seq(
            InnerTxnBuilder.Begin(),
            InnerTxnBuilder.MethodCall(
                app_id=app.application_id(),                
                method_signature=application.get_method_signature(Child.close_out),
                args=[Txn.sender(), asset],
            ),
            InnerTxnBuilder.Next(),
            InnerTxnBuilder.MethodCall(
                app_id=app.application_id(),                
                method_signature=application.get_method_signature(Child.pop_asset),
                args=[creator, asset],
            ),
            InnerTxnBuilder.Submit(),
            )

    @external
    def remove_receiver_vault(self, app: abi.Application, receiver: abi.Address, creator: abi.Account):
        return Seq(
                has_creator_address := AppParam.creator(self.id),
                (creator_address := abi.Address()).set(has_creator_address.value()),
                # holding := asset.holding(app_address.value()).balance(),
                # (opted_in := abi.Uint64()).set(holding.hasValue()),
                InnerTxnBuilder.Begin(),
                InnerTxnBuilder.MethodCall(
                    app_id=app.application_id(),                
                    method_signature=application.get_method_signature(Child.delete),
                    args=[creator],
                ),
                InnerTxnBuilder.Next(),
                InnerTxnBuilder.SetFields(
                {
                    TxnField.type_enum: TxnType.Payment,
                    TxnField.amount: Int(118700),
                    TxnField.receiver: creator_address.get(),
                }),
                InnerTxnBuilder.Submit(),

                Pop(self.vault[receiver].delete()),
            )


#aDD global value for authentication address app master
if __name__ == "__main__":
    app = Master()
    app.generate_teal()
    app.dump("./artifacts")